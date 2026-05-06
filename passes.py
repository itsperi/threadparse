import ast
from model import ThreadTarget, Scope
from collections import defaultdict

class PCNodeVisitor(ast.NodeVisitor):
   '''
   Custom NodeVisitor that enables parent tracking for upwards recursion
   '''
   def generic_visit(self, node):
      for child in ast.iter_child_nodes(node):
         child.parent = node
         self.visit(child)


class ImportDetection(ast.NodeVisitor):
   '''
   This pass of the AST is meant to detect
   whether a file contains the threading import
   '''
   
   def __init__(self):
      self.uses_threading = False

   # checks normal "import ...,x,..." where x is a multithreading module
   def visit_Import(self, node):
      for alias in node.names:
         if alias.name in {"threading",
                           "_thread",
                           "concurrent.futures",
                           "multiprocessing.pool"}:
            self.uses_threading = True
            return
            
      self.generic_visit(node)

   # checks "from x import ..." where x is a multithreading module
   def visit_ImportFrom(self, node):
      if node.module in {"threading",
                         "_thread",
                         "concurrent",
                         "concurrent.futures",
                         "multiprocessing.pool"}:
         self.uses_threading = True
         return
      
      # Just in case
      for alias in node.names:
         if alias.name in {"Thread",
                           "start_new_thread",
                           "ThreadPoolExecutor",
                           "ThreadPool"}:
            self.uses_threading = True
            return
      self.generic_visit(node)
            
class SymbolPass(PCNodeVisitor):
   '''
   This pass of the AST is meant to gather the names and locations
   of global, nonlocal variables and function definitions to later 
   use as potential pieces of interest in threads, as well as 
   gathering info about class definitions for later resolution of method calls;
   we also capture rudimentary data about various ways threads are instantiated
   '''
   def __init__(self, model):
      self.model = model
      self.current_function = None
      self.current_class = None
      
   # Simply used to update the live class we're visiting
   def visit_ClassDef(self, node):
      previous_class = self.current_class
      self.current_class = node.name

      for base in node.bases:
        if self._is_thread_base(base):
            self.model.thread_subclasses.add(node.name)
            break
         
      self.generic_visit(node)
      self.current_class = previous_class

   # Functions are saved with their qualified name
   # which helps to resolve thread accesses that
   # use class data instead of simple module-level data
   def visit_FunctionDef(self, node):
      if self.current_class:
         qualified_name = f"{self.current_class}.{node.name}"
      else:
         qualified_name = node.name
      self.current_function = qualified_name
      
      self.model.functions[qualified_name] = node
      
      # If a function is defined within a class, 
      # we want to save the qualified name as well for later reference
      if self.current_class:
         self.model.method_to_class[qualified_name] = self.current_class
         self.model.class_methods \
            .setdefault(self.current_class, set()) \
            .add(qualified_name)
         
      previous = self.current_function
      self.current_function = node.name
      self.generic_visit(node)
      self.current_function = previous
      
   # Interestingly enough, asynch func defs are 
   # completely different nodes but they are treated the same
   visit_AsyncFunctionDef = visit_FunctionDef

   # add globals to model
   def visit_Global(self, node):
      for name in node.names:
         self.model.globals[name] = node
         
         # if we're in a function, 
         # tie that global to the function
         if self.current_function:
            self.model.function_globals \
               .setdefault(self.current_function, set()) \
               .add(name)

   # add nonlocals to model
   def visit_Nonlocal(self, node):
      for name in node.names:
         self.model.nonlocals[name] = node
         
         if self.current_function:
            self.model.function_nonlocals \
               .setdefault(self.current_function, set()) \
               .add(name)
               
   
   def visit_Assign(self, node):
      # Module-level variables are special in that
      # threads can access them without needing a global declaration, 
      # so we want to track them, especially since we need to 
      # verify any thread targets access them after their initial assignment
      if self.current_function is None:
         for target in node.targets:
            for name in self._extract_names(target):
               self.model.module_vars[name] = node
      
      # Here we capture any instantiations from function calls
      # that either alias to known threading constructs or are used as thread targets
      if isinstance(node.value, ast.Call):
         func = node.value.func
         is_executor = (
            isinstance(func, ast.Name) and func.id in ("ThreadPoolExecutor", "ThreadPool")
         ) or (
            isinstance(func, ast.Attribute) and func.attr in ("ThreadPoolExecutor", "ThreadPool")
         )
         # Greedily add assignments as executors
         if is_executor:
            for target in node.targets:
               for name in self._extract_names(target):
                  self.model.executors \
                     .setdefault(name, set()) \
                     .add(node.value)
                     
         is_thread = (
            isinstance(func, ast.Name) and func.id == "Thread"
         ) or (
            isinstance(func, ast.Attribute) and func.attr == "Thread"
         ) or (
            isinstance(func, ast.Name) and func.id in self.model.thread_subclasses
         ) or (
            isinstance(func, ast.Attribute) and func.attr in self.model.thread_subclasses
         )
         if is_thread:
            for target in node.targets:
               for name in self._extract_names(target):
                  self.model.thread_vars.add(name)
                  
         # We also capture Queue instantiations since they are thread-safe
         is_queue = (
            isinstance(func, ast.Name) and func.id in {"Queue", "SimpleQueue", "PriorityQueue"}
         ) or (
            isinstance(func, ast.Attribute) and func.attr in {"Queue", "SimpleQueue", "PriorityQueue"}
         )
         if is_queue:
            type_name = func.id if isinstance(func, ast.Name) else func.attr
            for target in node.targets:
               for name in self._extract_names(target):
                  self.model.var_types[name] = type_name
                           
      # Also capture info about class attribute instantiations
      if self.current_class and self.current_function:
         for target in node.targets:
            if (isinstance(target, ast.Attribute) 
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"):
               qualified = f"{self.current_class}.{target.attr}"
               self.model.class_attrs[qualified] = node

      self.generic_visit(node)
      
   # Threading constructs often use context managers, so we visit these as well
   def visit_With(self, node):
      for with_item in node.items:
         context_expr = with_item.context_expr
         if isinstance(context_expr, ast.Call):
            func = context_expr.func
            is_executor = (
               isinstance(func, ast.Name) and func.id in ("ThreadPoolExecutor", "ThreadPool")
            ) or (
               isinstance(func, ast.Attribute) and func.attr in ("ThreadPoolExecutor", "ThreadPool")
            )
            if is_executor:
               alias = with_item.optional_vars
               if isinstance(alias, ast.Name):
                  name = alias.id
                  self.model.executors \
                     .setdefault(name, set())\
                     .add(context_expr)   
      self.generic_visit(node)
      
   def _extract_names(self, node):
      if isinstance(node, ast.Name):
         return [node.id]
      elif isinstance(node, (ast.Tuple, ast.List)):
         names = []
         for elt in node.elts:
            names.extend(self._extract_names(elt))
         return names
      return []
   
   # Helper to check if a class inherits from threading.Thread
   def _is_thread_base(self, node):
      if isinstance(node, ast.Name) and node.id == "Thread":
         return True
      elif isinstance(node, ast.Attribute) and node.attr == "Thread":
         return True
      # Assumes the parent class is defined first, and in the same file
      elif isinstance(node, ast.Name) and node.id in self.model.thread_subclasses:
         return True
      else:
         return False

COLLECTION_CONSTRUCTORS = {"list", "dict", "set", "defaultdict", "OrderedDict", "Counter"}
QUEUE_TYPES = {"Queue", "SimpleQueue", "PriorityQueue"}
class TypeInferencePass(PCNodeVisitor):
   '''
   This pass of the AST is meant to gather types of
   variables (namely default data structures) created in the
   program so we can filter out the mutations to Python's 
   naturally unsafe default collections like lists, sets, and dicts
   '''
   def __init__(self, model) -> None:
      self.model = model
      self.current_function = None
      self.current_class = None

   # Standard scoping rules
   def visit_ClassDef(self, node) -> None:
      prev = self.current_class
      self.current_class = node.name
      self.generic_visit(node)
      self.current_class = prev

   def visit_FunctionDef(self, node) -> None:
      prev = self.current_function
      self.current_function = node.name
      self.generic_visit(node)
      self.current_function = prev
   
   visit_AsyncFunctionDef = visit_FunctionDef

   # Visit variable assignments, and populate var_types
   # If we're looking at class attributes, qualify their full name
   def visit_Assign(self, node) -> None:
      inferred = self._infer(node.value)
      if inferred:
         for target in node.targets:
            for name in self._extract_names(target):
               self.model.var_types[name] = inferred
            # Handle self.x = [] / self.x = {} / self.x = set()
            if (isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and self.current_class):
               qualified = f"{self.current_class}.{target.attr}"
               self.model.var_types[qualified] = inferred
               self.model.var_types[target.attr] = inferred
               
      self.generic_visit(node)

   # Properly annotated variables are different nodes, 
   # but are inferred more straightforwardly 
   def visit_AnnAssign(self, node) -> None:
      inferred = self._infer(node.value) if node.value else self._infer_annotation(node.annotation)
      if inferred:
         if isinstance(node.target, ast.Name):
            self.model.var_types[node.target.id] = inferred
            # Also store qualified name if we're in a class
            if self.current_class:
               qualified = f"{self.current_class}.{node.target.id}"
               self.model.var_types[qualified] = inferred
         elif (isinstance(node.target, ast.Attribute)
         and isinstance(node.target.value, ast.Name)
         and node.target.value.id == "self"
         and self.current_class):
            qualified = f"{self.current_class}.{node.target.attr}"
            self.model.var_types[qualified] = inferred
            self.model.var_types[node.target.attr] = inferred

      self.generic_visit(node)

   # Helper that takes a node and looks at the type
   def _infer(self, node) -> str | None:
      if node is None:
         return None
      if isinstance(node, (ast.List, ast.ListComp)):
         return "list"
      if isinstance(node, (ast.Dict, ast.DictComp)):
         return "dict"
      if isinstance(node, (ast.Set, ast.SetComp)):
         return "set"
      # Constructor calls: list(), dict(), set(), defaultdict(list), etc.
      if isinstance(node, ast.Call):
         return self._infer_call(node)
      if isinstance(node, ast.Subscript):
         if isinstance(node.slice, ast.Slice):
            return self._infer(node.value)

      # For operations like x = [] + [] or x = set() | set()
      if isinstance(node, ast.BinOp):
         if isinstance(node.op, ast.Add):
            left = self._infer(node.left)
            if left: return left
            return self._infer(node.right)
         if isinstance(node.op, ast.BitOr):
            return self._infer(node.left) or self._infer(node.right)
      return None

   def _infer_call(self, node):
      if isinstance(node.func, ast.Name):
         name = node.func.id
         if name in COLLECTION_CONSTRUCTORS:
            # Normalize defaultdict/OrderedDict()
            return {"defaultdict": "dict", "OrderedDict": "dict",
                  "Counter": "dict"}.get(name, name)
         if name in QUEUE_TYPES:
            return name
      elif isinstance(node.func, ast.Attribute):
         # Normalize collections.defaultdict(...)
         if node.func.attr in COLLECTION_CONSTRUCTORS:
            return {"defaultdict": "dict", "OrderedDict": "dict",
                  "Counter": "dict"}.get(node.func.attr, node.func.attr)
         if node.func.attr in QUEUE_TYPES:
            return node.func.attr
      return None

   def _infer_annotation(self, node):
      # Handles bare annotations like x: list
      if isinstance(node, ast.Name) and node.id in {"list", "dict", "set"}:
         return node.id
      # Handles subscript annotations like x: list[int]
      if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
         if node.value.id in {"List", "Dict", "Set", "list", "dict", "set"}:
            return node.value.id.lower()
         if node.value.id == "Optional":
            return self._infer_annotation(node.slice)
      # Handles union annotations like x: list | None
      if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
         left = self._infer_annotation(node.left)
         if left: return left
         return self._infer_annotation(node.right)

      return None

   def _extract_names(self, node):
      if isinstance(node, ast.Name):
         return [node.id]
      elif isinstance(node, (ast.Tuple, ast.List)):
         names = []
         for elt in node.elts:
            names.extend(self._extract_names(elt))
         return names
      return []

class ScopePass(PCNodeVisitor):
   '''
   This pass of the AST is meant to go over the function
   definitions, variable writes in each function/scope
   and define the stack scope for each one
   '''
   def __init__(self, model):
      self.model = model
      self.scope_stack: list[Scope] = []
      self.current_class = None

   def current_scope(self):
      return self.scope_stack[-1] if self.scope_stack else None

   def visit_ClassDef(self, node):
      previous = self.current_class
      self.current_class = node.name
      self.generic_visit(node)
      self.current_class = previous

   '''
   Whenever we enter a function definition, we create a new scope
   and add the parameters as local variables (this is somewhat pessimistic, 
   as it can be a copy of a variable or a reference). When we exit,
   we pop the scope and save it in the program model for later use.
   '''
   def visit_FunctionDef(self, node):
      if self.current_class:
         qualified_name = f"{self.current_class}.{node.name}"
      else:
         qualified_name = node.name
         
      scope = Scope(parent=self.current_scope())
      self.scope_stack.append(scope)

      # parameters are locals
      for arg in node.args.args:
         scope.locals.add(arg.arg)

      self.generic_visit(node)

      self.model.function_scopes[qualified_name] = scope
      self.scope_stack.pop()
   
   visit_AsyncFunctionDef = visit_FunctionDef

   def visit_Name(self, node):
      scope = self.current_scope()
      if scope and isinstance(node.ctx, ast.Store):
         if node.id not in scope.globals and node.id not in scope.nonlocals:
            scope.locals.add(node.id)

   def visit_Global(self, node):
      scope = self.current_scope()
      if scope:
         scope.globals.update(node.names)

   def visit_Nonlocal(self, node):
      scope = self.current_scope()
      if scope:
         scope.nonlocals.update(node.names)
         
   def visit_For(self, node):
      scope = self.current_scope()
      if scope:
         for name in self._extract_names(node.target):
            scope.locals.add(name)
      self.generic_visit(node)

   def visit_With(self, node):
      scope = self.current_scope()
      if scope:
         for item in node.items:
            if item.optional_vars:
               for name in self._extract_names(item.optional_vars):
                  scope.locals.add(name)
      self.generic_visit(node)

   def visit_ExceptHandler(self, node):
      scope = self.current_scope()
      if scope and node.name:
         scope.locals.add(node.name)
      self.generic_visit(node)

   # Comprehensions create their own scope in Python 3, 
   # so we don't want their variables leaking into the enclosing scope
   def visit_ListComp(self, node): 
      self._visit_comprehension(node)
   def visit_SetComp(self, node): 
      self._visit_comprehension(node)
   def visit_DictComp(self, node): 
      self._visit_comprehension(node)
   def visit_GeneratorExp(self, node): 
      self._visit_comprehension(node)

   def _extract_names(self, node):
      if isinstance(node, ast.Name):
         return [node.id]
      elif isinstance(node, (ast.Tuple, ast.List)):
         names = []
         for elt in node.elts:
            names.extend(self._extract_names(elt))
         return names
      return []
   
   # As long as we create a new scope for the comprehension
   # and don't add any of its variables to the enclosing scope,
   # we can just visit it normally without worrying about the details
   def _visit_comprehension(self, node):
      comp_scope = Scope(parent=self.current_scope())
      self.scope_stack.append(comp_scope)
      for generator in node.generators:
         for name in self._extract_names(generator.target):
            comp_scope.locals.add(name)
      self.generic_visit(node)
      self.scope_stack.pop()

class ThreadPass(PCNodeVisitor):
   '''
   This pass of the AST is meant to gather 
   info about which functions are designated 
   as Thread targets and saves their nodes 
   and other relevant information for later use
   '''
   def __init__(self, model):
      self.model = model
      self.current_class = None
      
   def visit_ClassDef(self, node):
      previous = self.current_class
      self.current_class = node.name
      self.generic_visit(node)
      self.current_class = previous
      
   # We look for both explicit Thread(target=...) cases 
   # and classes that inherit from Thread and define run()
   def visit_FunctionDef(self, node):
      if (self.current_class
      and self.current_class in self.model.thread_subclasses
      and node.name == "run"):
         qualified_name = f"{self.current_class}.run"
         if qualified_name not in self.model.seen_targets \
         and qualified_name in self.model.functions:
            target = ThreadTarget(name=qualified_name,
                                  class_name=self.current_class,
                                  node=node,
                                  parent_target=None,
                                  root_target=qualified_name)
            self.model.thread_targets.append(target)
            self.model.seen_targets.add(qualified_name)

      self.generic_visit(node)

   visit_AsyncFunctionDef = visit_FunctionDef
      
   def _check_threading(self, node, kw):
      if kw.arg == "target":
         name = self._qualify(kw.value)
         # Only targets known as defined functions are added
         if name and name not in self.model.seen_targets \
         and name in self.model.functions.keys():
            target = ThreadTarget(name=name, 
                                  class_name=self.current_class, 
                                  node=node,
                                  parent_target=None,
                                  root_target=name)
            self.model.thread_targets.append(target)
            self.model.seen_targets.add(name)
   
   def _check_thread(self, arg):
      # The first argument to start_new_thread() is the target function
      name = self._qualify(arg)         
      if name and name not in self.model.seen_targets \
      and name in self.model.functions.keys():
         target = ThreadTarget(name=name, 
                               class_name=self.current_class, 
                               node=arg,
                               parent_target=None,
                               root_target=name)
         self.model.thread_targets.append(target)
         self.model.seen_targets.add(name)
         
   def _check_executor(self, arg, executor_node=None):
      name = self._qualify(arg)
      if name and name not in self.model.seen_targets \
      and name in self.model.functions.keys():
         # Check if the call is on a known executor
         executor_name = self._qualify(executor_node) if executor_node else None
         if executor_name and executor_name in self.model.executors:
               target = ThreadTarget(name=name, 
                                     class_name=self.current_class, 
                                     node=arg,
                                     parent_target=None,
                                     root_target=name)
               self.model.thread_targets.append(target)
               self.model.seen_targets.add(name)

   def visit_Call(self, node):
      # Look for calls to Thread(target=...)
      if isinstance(node.func, ast.Name):
         if node.func.id == "Thread":
            for kw in node.keywords:
               self._check_threading(node, kw)

      # Most cases, we are calling attribute methods
      elif isinstance(node.func, ast.Attribute):
         if node.func.attr == "Thread":
            for kw in node.keywords:
               self._check_threading(node, kw)
               
         # or for _threading.start_new_thread(...) cases
         elif node.func.attr == "start_new_thread":
            if node.args:
               self._check_thread(node.args[0]) 
         
         # or for executor.submit/map(...) cases
         elif node.func.attr in ("submit", "map", 
                                 "imap", "imap_unordered", "starmap", "apply_async"):
            if node.args:
               self._check_executor(node.args[0], node.func.value)
               
         elif node.func.attr == "start":
            receiver = node.func.value
            if isinstance(receiver, ast.Name):
               if receiver.id in self.model.thread_vars:
                  self.model.thread_start_lines.append(node.lineno)

      self.generic_visit(node)
      
   def _qualify(self, node):
      if isinstance(node, ast.Attribute):
         method = node.attr
         # Resolve self.method -> "ClassName.method"
         if isinstance(node.value, ast.Name) and node.value.id == "self":
            if self.current_class:
               qualified = f"{self.current_class}.{method}"
               if qualified in self.model.functions:
                  return qualified
                  
         for _class, methods in self.model.class_methods.items():
            if method in methods:
               return f"{self.current_class}.{method}" if self.current_class else method
         return method

      if isinstance(node, ast.Name):
         return node.id
      
      if isinstance(node, ast.Lambda):
         body = node.body
         if isinstance(body, ast.Call):
            return self._qualify(body.func)

      return None

class CallGraphPass(PCNodeVisitor):
   '''
   This pass of the AST is meant to 
   build a call graph so we can 
   find "transitive ThreadTargets"
   '''
   def __init__(self, model):
      self.model = model
      self.current_function = None
      self.current_class = None

   def visit_ClassDef(self, node):
      prev = self.current_class
      self.current_class = node.name
      self.generic_visit(node)
      self.current_class = prev

   def visit_FunctionDef(self, node):
      if self.current_class:
         fname = f"{self.current_class}.{node.name}"
      else:
         fname = node.name
         
      self.model.call_graph.setdefault(fname, set())

      prev = self.current_function
      self.current_function = fname
      self.generic_visit(node)
      self.current_function = prev
      
   visit_AsyncFunctionDef = visit_FunctionDef

   # Find any function calls that exist 
   # within functions and add them to the call graph
   def visit_Call(self, node):
      if not self.current_function:
         return

      callee = None

      if isinstance(node.func, ast.Name):
         callee = node.func.id

      elif isinstance(node.func, ast.Attribute):
         bare = node.func.attr
         if self.current_class:
            qualified = f"{self.current_class}.{bare}"
            callee = qualified if qualified in self.model.functions else bare
         else:
            callee = bare

      if callee and callee in self.model.functions:
         self.model.call_graph[self.current_function].add(callee)

      self.generic_visit(node)

class ThreadExpansion:
   '''
   This class is meant to update the
   program model so that we include 
   the indirect thread targets
   '''
   def __init__(self, model):
      self.model = model

   def expand(self):
      worklist = [t.name for t in self.model.thread_targets]
      visited = set(worklist)

      while worklist:
         current = worklist.pop()

         for callee in self.model.call_graph.get(current, []):
            # Only consider known functions
            if callee in self.model.functions and callee not in visited:
               visited.add(callee)
               worklist.append(callee)

               # Add as synthetic thread target
               class_name = self.model.method_to_class.get(callee)
               root_target = current.root_target if current in self.model.thread_targets else current
               self.model.thread_targets.append(
                  ThreadTarget(name=callee, 
                               class_name=class_name,
                               node=self.model.functions[callee],
                               parent_target=current,
                               root_target=root_target)
               )                    

# We only care about list, set, and dict mutations
MUTATING_METHODS = {
   "append", "extend", "insert", "remove", "pop", "clear", "sort", "reverse", 
   "add", "discard", "pop", "clear", "update", "intersection_update", "difference_update", "symmetric_difference_update", 
   "clear", "pop", "popitem", "setdefault", "update", 
}
class TargetPass(PCNodeVisitor):
   '''
   This pass of the AST is meant to target specifically the 
   functions designated as ThreadTargets and gather information
   about the variable reads, writes, function calls with 
   potential side effects, and subscript/attribute accesses 
   '''
   def __init__(self, 
                model = None, 
                scope: Scope = None, 
                class_name: str = None, 
                var_types = None):
      self.model = model
      self.scope = scope
      self.class_name = class_name
      self.var_types = var_types
      self.reads = defaultdict(list)
      self.writes = defaultdict(list)
      self.calls = defaultdict(list)
      
   # For attribute accesses, we need to 
   # extract the entire chain: class.list.append()
   def get_full_attr_name(self, node):
      parts = []
      current = node

      while isinstance(current, ast.Attribute):
         parts.append(current.attr)
         current = current.value

      if isinstance(current, ast.Name):
         parts.append(current.id)
         return ".".join(reversed(parts))

      return None
      
   # For reads/writes within thread targets
   def visit_Name(self, node):
      parent = getattr(node, "parent", None)

      # If this Name is part of ANY attribute chain, ignore it
      if isinstance(parent, ast.Attribute) and parent.value is node:
         return

      # If this Name is part of a call (foo()), ignore it
      if isinstance(parent, ast.Call) and parent.func is node:
         return
      
      if node.id in self.scope.locals:
         # If this is a local variable, we can ignore it for cross-thread sharing purposes
         self.generic_visit(node)
         return

      # Otherwise only standalone variables count
      if isinstance(node.ctx, ast.Load):
         self.reads[node.id].append(node)
      elif isinstance(node.ctx, ast.Store):
         self.writes[node.id].append(node)
   
   # For shorthand exprs like x += 1
   def visit_AugAssign(self, node):
      target = node.target

      if isinstance(target, ast.Name):
         self.reads[target.id].append(node)
         self.writes[target.id].append(node)

      elif isinstance(target, ast.Attribute):
         full_name = self.get_full_attr_name(target)
         if full_name and full_name.startswith("self.") and self.class_name:
            full_name = f"{self.class_name}.{full_name[5:]}"
         if full_name:
            self.reads[full_name].append(node)
            self.writes[full_name].append(node)

      elif isinstance(target, ast.Subscript):
         if isinstance(target.value, ast.Name):
            self.reads[target.value.id].append(node)
            self.writes[target.value.id].append(node)
         elif isinstance(target.value, ast.Attribute):
            full_name = self.get_full_attr_name(target.value)
            if full_name and full_name.startswith("self.") and self.class_name:
               full_name = f"{self.class_name}.{full_name[5:]}"
            if full_name:
               self.reads[full_name].append(node)
               self.writes[full_name].append(node)
               
      # Don't call generic_visit, we'll double count
      # self.generic_visit(node)     
      self.visit(node.value)
      
   # For attribute accesses like class.x
   def visit_Attribute(self, node):
      full_name = self.get_full_attr_name(node)
      if full_name and full_name.startswith("self.") and self.class_name:
         full_name = f"{self.class_name}.{full_name[5:]}"

      if full_name:
         # If this attribute is part of a Call, classify ONLY as call, not read
         if isinstance(node.parent, ast.Call) and node.parent.func is node:
               return
            
         root = full_name.split(".")[0]
         
         if self.scope and root in self.scope.locals and root not in {"self", "cls"}:
            # If the root of this attribute is a local variable, 
            # we can ignore it for cross-thread sharing purposes
            self.generic_visit(node)
            return

         if isinstance(node.ctx, ast.Load):
            self.reads[full_name].append(node)
         elif isinstance(node.ctx, ast.Store):
            self.writes[full_name].append(node)

      self.generic_visit(node)
      
   # For method calls that may be
   # mutating a shared data structure
   def visit_Call(self, node):
      func_name = None
      
      if isinstance(node.func, ast.Name):
         func_name = node.func.id
         if func_name in MUTATING_METHODS:
            self.calls[func_name].append(node)

      elif isinstance(node.func, ast.Attribute):
         func_name = self.get_full_attr_name(node.func)
         if func_name and func_name.startswith("self.") and self.class_name:
            func_name = f"{self.class_name}.{func_name[5:]}"
         method = node.func.attr
         obj = node.func.value

         # Record method calls that mutate, 
         # but filter out mutations to known 
         # thread-safe queue types and collections 
         # that are locally defined within the function
         if method in MUTATING_METHODS:
            if isinstance(obj, ast.Name):
               obj_type = self.var_types.get(obj.id)
               if obj_type not in QUEUE_TYPES and (obj_type in {"list", "set", "dict"} or obj_type is None):
                  self.calls[func_name].append(node)
                  if self.scope and obj.id not in self.scope.locals:
                     self.writes[obj.id].append(node)
                        
            elif isinstance(obj, ast.Attribute):
               full_obj = self.get_full_attr_name(obj)
               if full_obj and full_obj.startswith("self.") and self.class_name:
                  full_obj = f"{self.class_name}.{full_obj[5:]}"
               if full_obj:
                  # Check if any prefix resolves to a queue type, e.g. "_HandlerThread._queue.queue"
                  # should be suppressed because "_HandlerThread._queue" is a Queue
                  obj_root = ".".join(full_obj.split(".")[:2]) 
                  if (self.var_types.get(full_obj) in QUEUE_TYPES
                  or self.var_types.get(obj_root) in QUEUE_TYPES):
                     pass
                  else:
                     self.calls[full_obj + "." + method].append(node)
                     self.writes[full_obj].append(node)

      self.generic_visit(node)
      
   # For subscript reads/writes
   def visit_Subscript(self, node):
      if isinstance(node.ctx, ast.Store):
         if isinstance(node.value, ast.Name):
            self.writes[node.value.id].append(node)
         elif isinstance(node.value, ast.Attribute):   
            full_name = self.get_full_attr_name(node.value)
            if full_name and full_name.startswith("self.") and self.class_name:
               full_name = f"{self.class_name}.{full_name[5:]}"
            if full_name:
               self.writes[full_name].append(node)
            
      elif isinstance(node.ctx, ast.Load):
         if isinstance(node.value, ast.Name):
            self.reads[node.value.id].append(node)
         elif isinstance(node.value, ast.Attribute):
            full_name = self.get_full_attr_name(node.value)
            if full_name and full_name.startswith("self.") and self.class_name:
               full_name = f"{self.class_name}.{full_name[5:]}"
            if full_name:
               self.reads[full_name].append(node)
               
   # for del list[x] or del list[:]
   def visit_Delete(self, node):
      for target in node.targets:
         if isinstance(target, ast.Subscript):
            if isinstance(target.value, ast.Name):
               self.writes[target.value.id].append(node)
            elif isinstance(target.value, ast.Attribute):
               full_name = self.get_full_attr_name(target.value)
               if full_name and full_name.startswith("self.") and self.class_name:
                  full_name = f"{self.class_name}.{full_name[5:]}"
               if full_name:
                  self.writes[full_name].append(node)
         # del list (the variable itself)
         elif isinstance(target, ast.Name):
            self.writes[target.id].append(node)

      self.generic_visit(node)
      
   def visit_ListComp(self, node):
      self._visit_comprehension(node)
   def visit_SetComp(self, node):
      self._visit_comprehension(node)
   def visit_DictComp(self, node):
      self._visit_comprehension(node)
   def visit_GeneratorExp(self, node):
      self._visit_comprehension(node)

   def _visit_comprehension(self, node):
      saved = set(self.scope.locals)
      for generator in node.generators:
         for name in self._extract_names(generator.target):
            self.scope.locals.add(name)
      self.generic_visit(node)
      self.scope.locals = saved

   def _extract_names(self, node):
      if isinstance(node, ast.Name):
         return [node.id]
      elif isinstance(node, (ast.Tuple, ast.List)):
         names = []
         for elt in node.elts:
            names.extend(self._extract_names(elt))
         return names
      return []


class TargetUpdate:
   '''
   This class is meant to update the program model
   with the relevant data collected from the thread pass
   '''
   def __init__(self, model):
      self.model = model
      
   def update_thread_accesses(self):
      # if not self.model.thread_targets:
         # print("No thread targets found...")
      for target in self.model.thread_targets:
         target_node = self.model.functions[target.name]
         target_scope = self.model.function_scopes.get(target.name)
         target_class = target.class_name
         
         parser = TargetPass(model = self.model, 
                             scope = target_scope, 
                             class_name = target_class,
                             var_types = self.model.var_types)
         parser.visit(target_node)
         
         target.reads = parser.reads
         target.writes = parser.writes
         target.calls = parser.calls

class ClassResolutionPass(PCNodeVisitor):
   '''
   This pass of the AST is meant to check 
   targets belonging to a class and capture
   info about instance attributes, as thread
   detection for thread targets within classes
   is handled slightly differently than in the ThreadPass
   '''
   def __init__(self, model):
      self.model = model
      
   # A method is reachable from another thread if it's called by the target
   # or if it's called by a method that is reachable from the target
   def _is_reachable_from_other_thread(self, method, target):
      visited = set()
      to_visit = [target.name]

      while to_visit:
         current = to_visit.pop()
         if current in visited:
            continue
         visited.add(current)

         for callee in self.model.call_graph.get(current, []):
            if callee == method:
               return True
            to_visit.append(callee)

      return False

   # For each target that belongs to a class, 
   # we look at all the sibling methods in the same class
   # and check if any of them are reachable from another thread. 
   # If they are, we parse them with TargetPass and merge their 
   # reads/writes into the target's sibling_reads/sibling_writes 
   def resolve_classes(self):
      for target in self.model.thread_targets:
         # Extract class name from qualified target name
         if "." not in target.name:
            continue

         class_name = target.class_name
         sibling_methods = self.model.class_methods.get(class_name, set())

         for method in sibling_methods:
            qualified = f"{class_name}.{method}"

            # Skip the target itself and __init__
            if qualified == target.name or method == "__init__":
               continue
            
            if not self._is_reachable_from_other_thread(qualified, target):
               continue

            method_node = self.model.functions.get(qualified)
            if not method_node:
               continue

            scope = self.model.function_scopes.get(qualified)
            parser = TargetPass(model=self.model, 
                                scope=scope, 
                                class_name=class_name, 
                                var_types=self.model.var_types)
            parser.visit(method_node)

            # Merge r/w from sibling methods into the target's writes
            # Tag them so we know they're from a sibling, not the target itself
            for var, nodes in parser.writes.items():
               if var.startswith("self.") or var.startswith(f"{class_name}."):
                  target.sibling_writes \
                     .setdefault(var, {}) \
                     .setdefault(method, []) \
                     .extend(nodes)
            
            for var, nodes in parser.reads.items():
               if var.startswith("self.") or var.startswith(f"{class_name}."):
                  target.sibling_reads \
                     .setdefault(var, {}) \
                     .setdefault(method, []) \
                     .extend(nodes)
                        
class SharedUpdate(PCNodeVisitor):
   '''
   This pass of the AST is meant to 
   analyze variables/data structures 
   across the entire program and detect 
   any that are shared among ThreadTargets
   '''
   def __init__(self, model):
      self.model = model
      # We should store the nodes of the reads/writes 
      # in the form: var -> target(s) -> list(s) of nodes for easier analysis later
      self.var_reads: dict[str, dict[str, list[ast.AST]]] = defaultdict(lambda: defaultdict(list))
      self.var_writes: dict[str, dict[str, list[ast.AST]]] = defaultdict(lambda: defaultdict(list))
      # May God smite my children for what I just wrote
      
   def _reachable_from_targets(self):
      reachable = set()
      stack = [t.name for t in self.model.thread_targets]
      
      while stack:
         current = stack.pop()
         if current in reachable:
            continue
         reachable.add(current)
         
         for callee in self.model.call_graph.get(current, []):
            stack.append(callee)

      return reachable
   
   def _is_safely_initialized(self, var, writer_targets, earliest_start):
      if writer_targets - {"__main__"}:
         return False

      main_nodes = self.var_writes[var].get("__main__", [])
      if not main_nodes:
         return False

      # Every write must occur before the first thread.start()
      return all(n.lineno < earliest_start for n in main_nodes)
      
   def _populate(self):
      # In each target, we have dicts of reads/writes: var -> list of nodes
      target_names = {t.name for t in self.model.thread_targets}
      reachable = self._reachable_from_targets()
      
      # First gather reads/writes from the thread targets themselves
      # We set tname to the parent target if it exists, 
      # so that we can merge sibling methods into the same "target" for sharing purposes
      for target in self.model.thread_targets:
         tname = target.parent_target if target.parent_target is not None else target.name
         for var, nodes in target.reads.items():
            self.var_reads[var][tname].extend(nodes)
            
         for var, nodes in target.writes.items():
            self.var_writes[var][tname].extend(nodes)
               
      for fname, fnode in self.model.functions.items():
         if fname in target_names or fname.endswith(".__init__"):
            continue
         if fname in reachable:
            continue
         
         scope = self.model.function_scopes.get(fname)
         class_name = self.model.method_to_class.get(fname)
         parser = TargetPass(scope=scope, 
                             class_name=class_name,
                             var_types=self.model.var_types)
         parser.visit(fnode)

         for var, nodes in parser.writes.items():
               self.var_writes[var][f"{fname}"].extend(nodes)
         for var, nodes in parser.reads.items():
               self.var_reads[var][f"{fname}"].extend(nodes)
               
      # We need to consider main thread scope
      for var, node in self.model.module_vars.items():
         self.var_writes[var]["__main__"].append(node)

   def _update(self):
      earliest_start = min(self.model.thread_start_lines, default=float('inf'))
      shared = {}
      all_vars = set(self.var_reads.keys()) | set(self.var_writes.keys())
      
      for var in all_vars:
         reader_targets = set(self.var_reads[var].keys())
         writer_targets = set(self.var_writes[var].keys())
         involved_targets = reader_targets | writer_targets
         
         # If a variable is accessed by multiple targets, it's potentially shared.
         # However, we can ignore it if it's only written by the main thread 
         # and all writes occur before the first thread starts.
         if len(involved_targets) > 1 and len(writer_targets) > 0:
            if self._is_safely_initialized(var, writer_targets, earliest_start):
               continue
            
            shared[var] = {
               "reads": {t: self.var_reads[var][t] for t in reader_targets},
               "writes": {t: self.var_writes[var][t] for t in writer_targets}
            }
            
      self.model.shared_vars = shared
   
   def update_shared_vars(self):
      self._populate()
      self._update()

class CriticalPass:
   '''
   This pass of the AST is meant to go over the thread targets
   identified earlier along with the relevant information
   from the previous passes and detect novel behavior that
   may be considered thread-unsafe and prepare output in 
   json format to analyze later
   '''
   def __init__(self, model, mode: str | None = None):
      self.model = model
      self.mode = mode

   def _is_with_locking(self, node) -> bool:
      locks = ("lock", "acquire", "rlock", "mutex", "semaphore")
      for item in node.items:
         ctx = item.context_expr
         
         if isinstance(ctx, ast.Name):
            for lock in locks:
               if lock in ctx.id.lower():
                  return True
                  
         elif isinstance(ctx, ast.Attribute):
            for lock in locks:
               if lock in ctx.attr.lower():
                  return True
                  
         elif isinstance(ctx, ast.Call):
            func = ctx.func
            if isinstance(func, ast.Attribute):
               if any(lock in func.attr.lower() for lock in locks):
                  return True
            elif isinstance(func, ast.Name):
               if any(lock in func.id.lower() for lock in locks):
                  return True
               
      return False
      
   def _is_inside_with(self, node) -> bool:
      current = node
      while hasattr(current, "parent") and current.parent:
         if isinstance(current.parent, ast.With):
            if self._is_with_locking(current.parent):
               return True
         current = current.parent
      return False     
   
   def _is_lock_call(self, node) -> bool:
      if not isinstance(node, ast.Expr):
         return False

      call = node.value
      if not isinstance(call, ast.Call):
         return False

      if isinstance(call.func, ast.Attribute):
         if call.func.attr.lower() in ("lock", "acquire"):
            return True
         
         if isinstance(call.func.value, ast.Attribute):
            return call.func.value.attr.lower() in ("lock", "acquire")

      return False

   def _is_unlock_call(self, node) -> bool:
      if not isinstance(node, ast.Expr):
         return False

      call = node.value
      if not isinstance(call, ast.Call):
         return False

      if isinstance(call.func, ast.Attribute):
         return call.func.attr.lower() in ("unlock", "release")

      return False
   
   def _get_parent_statement(self, node) -> ast.stmt | None:
      current = node
      while current and not isinstance(current, ast.stmt):
         current = getattr(current, "parent", None)
      return current
   
   def _is_between_lock_unlock(self, node) -> bool:
      stmt = self._get_parent_statement(node)
      current = stmt

      while current and hasattr(current, "parent") and current.parent:
         parent = current.parent
         if not hasattr(parent, "body"):
            current = parent
            continue

         body = parent.body
         if current not in body:
            current = parent
            continue
         
         idx = body.index(current)
         lock_found = False

         # search backwards for lock
         for i in range(idx - 1, -1, -1):
            if self._is_unlock_call(body[i]):
               break
            if self._is_lock_call(body[i]):
               lock_found = True
               break

         # search forward for unlock
         if lock_found:
            # check try finallys   
            if isinstance(current, ast.Try) \
            and any(self._is_unlock_call(s) for s in current.finalbody):
               return True
             
            for i in range(idx + 1, len(body)):
               if self._is_lock_call(body[i]):
                  break
               if self._is_unlock_call(body[i]):
                  return True
         current = parent
         
      return False
   
   # A node is protected if it's either inside a with statement that involves locking
   # or if it's between a lock and unlock call in the same function
   def _is_protected(self, node) -> bool:
      return self._is_inside_with(node) or self._is_between_lock_unlock(node)
   
   # Extra step to check one level up for indirect targets, 
   # since they may be protected by locks at the call site level
   def _is_node_protected(self, node: ast.AST, target: ThreadTarget) -> bool:
      return self._is_protected(node) or self._call_sites_are_protected(target)
   
   def _is_external(self, name, scope: Scope) -> bool:
      if name in scope.locals:
         return False

      # We need to make sure that globals and nonlocals
      # respect the scope rules, otherwise we may have false positives
      
      if name in scope.globals or name in scope.nonlocals:
         return True

      parent = scope.parent
      while parent:
         if name in parent.locals:
            return False
         parent = parent.parent

      return (
         name in self.model.globals or 
         name in self.model.nonlocals or
         name in self.model.module_vars
      )
   
   def _is_shared_variable(self, var, scope: Scope) -> bool:
      if scope is None:
         return False
      
      if var in scope.locals:
         return False
      
      # If it's in the cross-thread shared vars map, it's shared
      if var in self.model.shared_vars:
         return True
      
      # Only treat global/nonlocal as shared if this scope explicitly declares it
      if var in scope.globals or var in scope.nonlocals:
         return True
      
      # Otherwise defer to the program model
      return self._is_external(var, scope)
   
   def _get_receiver(self, func_key: str) -> str | None:
      parts = func_key.rsplit(".", 1)
      if len(parts) < 2:
         return None
      return parts[0]  # everything before the last dot
   
   def _receiver_is_shared(self, receiver: str, scope: Scope) -> bool:
      if receiver is None:
         return False
      
      # Direct match in shared_vars (e.g. 'results', 'MyClass.data')
      if receiver in self.model.shared_vars:
         return True
      
      # Also check the root name for attribute chains like 'self.data'
      root = receiver.split(".")[0]
      if root in self.model.shared_vars:
         return True
      
      # Fall back to the general shared-variable check on the root
      return self._is_shared_variable(root, scope)
   
   def _get_shared_accesses(self, target) -> tuple[set[str], set[str]]:
      shared_reads = {
         var for var in target.reads
         if self._is_shared_variable(var, scope=self.model.function_scopes.get(target.name))
      }

      shared_writes = {
         var for var in target.writes
         if self._is_shared_variable(var, scope=self.model.function_scopes.get(target.name))
      }

      return shared_reads, shared_writes
   
   def _classify_variable(self, var) -> str:
      if var in self.model.globals:
         return "GLOBAL"
      elif var in self.model.nonlocals:
         return "NONLOCAL"
      elif var in self.model.module_vars:
         return "MAIN_SCOPE"
      elif var in self.model.class_attrs:
         return "CLASS_ATTR"
      elif var in self.model.shared_vars:
         return "CROSS_THREAD"
      return "OTHER"
   
   def _node_loc(self, node) -> dict:
      return {
         "line": getattr(node, "lineno", None),
         "col":  getattr(node, "col_offset", None),
      }
      
   def _serialize_access_map(self, access_map: dict[str, list]) -> dict:
      return {
         var: [self._node_loc(n) for n in nodes]
         for var, nodes in access_map.items()
      }
      
   def _serialize_shared_vars(self) -> dict:
      result = {}
      for var, info in self.model.shared_vars.items():
         result[var] = {
            "type": self.model.var_types.get(var, "other"),
            "classification": self._classify_variable(var),
            "reads":  {t: [self._node_loc(n) for n in nodes]
                     for t, nodes in info["reads"].items()},
            "writes": {t: [self._node_loc(n) for n in nodes]
                     for t, nodes in info["writes"].items()},
         }
      return result
   
   def _call_sites_are_protected(self, target: ThreadTarget) -> bool:
      """
      For an indirect target, check whether every call site to it
      within its parent target's function body is lock-protected.
      If so, the callee's body is transitively covered.
      """
      # Ignore non-transitive targets
      if not target.parent_target:
         return False

      parent_node = self.model.functions.get(target.parent_target)
      if not parent_node:
         return False

      # Match on bare name to handle both `foo()` and `self.foo()` 
      bare = target.name.rsplit(".", 1)[-1]
      call_sites = [
         node for node in ast.walk(parent_node)
         if isinstance(node, ast.Call) and (
               (isinstance(node.func, ast.Name) and node.func.id == bare) or
               (isinstance(node.func, ast.Attribute) and node.func.attr == bare)
         )
      ]

      # No call sites found means we can't confirm protection
      if not call_sites:
         return False

      # ALL call sites must be protected
      return all(self._is_protected(cs) for cs in call_sites)
 
   def _serialize_target(self, target: ThreadTarget) -> dict:
      scope = self.model.function_scopes.get(target.name)
      shared_reads, shared_writes = self._get_shared_accesses(target)
      unprotected_calls = self._get_unprotected_calls(target)

      # Partition accesses into protected / unprotected
      def split_protected(var_set, access_map, access_type):  # access_type = "reads" or "writes"
         out = {}
         tname = target.parent_target or target.name
         for var in var_set:
            shared_info = self.model.shared_vars.get(var)
            # Fall back to access_map if var isn't in shared_vars (e.g. nonlocal/global only)
            if shared_info and tname in shared_info[access_type]:
                  nodes = shared_info[access_type][tname]
            else:
                  nodes = access_map.get(var, [])

            unprotected, protected = [], []
            for node in nodes:
                  if not self._is_node_protected(node, target):
                     unprotected.append(self._node_loc(node))
                  else:
                     protected.append(self._node_loc(node))

            out[var] = {
                  "unprotected":      unprotected,
                  "protected":        protected,
                  "type":             self.model.var_types.get(var),
                  "classification":   self._classify_variable(var),
            }
            
         return out

      call_locs = {}
      for func, nodes in target.calls.items():
         if func not in unprotected_calls:
               continue
         receiver = self._get_receiver(func)
         tname = target.parent_target or target.name
         if receiver and not self._receiver_is_shared(
            receiver, self.model.function_scopes.get(tname)):
               continue
         call_locs[func] = {
               "protected":   [self._node_loc(n) for n in nodes if self._is_protected(n)],
               "unprotected": [self._node_loc(n) for n in nodes if not self._is_protected(n)],
         }

      return {
         "name":           target.name,
         "class":          target.class_name if target.class_name else None,
         "parent_target":  target.parent_target if target.parent_target else None,
         "root_target":    target.root_target,
         "shared_reads":   split_protected(shared_reads,  target.reads, "reads"),
         "shared_writes":  split_protected(shared_writes, target.writes, "writes"),
         "mutating_calls": call_locs,
      }
      
   def to_json(self, violations: set[str], found_bad_call: bool) -> dict:
      return {
         "unsafe":       bool(violations) or found_bad_call,
         "violations":   sorted(violations),
         "shared_vars":  self._serialize_shared_vars(),
         "thread_targets": [self._serialize_target(t) for t in self.model.thread_targets],
      }
   
   def _get_unprotected_calls(self, target) -> set[str]:
      return {func for func, nodes in target.calls.items() if any(not self._is_protected(node) for node in nodes)}
   
   def _print_thread_header(self, target, shared_reads, shared_writes, calls) -> None:
      print(f"\n Thread routine: {target.name}")
      
      if shared_reads:
         print("   Reads shared variables:")
         for var in shared_reads:
            print(f"      {var} [{self._classify_variable(var)}]")
         print() 
            
      if shared_writes:
         print("   Writes shared variables:")
         for var in shared_writes:
            print(f"      {var} [{self._classify_variable(var)}]")
         print() 

      if calls:
         print("   Calls functions:", calls)
         
   def _print_other_threads(self, var, current, readers, writers) -> None:
      other_readers = [t for t in readers if t != current]
      other_writers = [t for t in writers if t != current]

      if other_readers:
         print(f"         Other readings of {var}:")
         for reader in other_readers:
            print(f"            - {reader} reads {var} in lines {[n.lineno for n in readers[reader]]}")
      if other_writers:
         print(f"         Other writings of {var}:")
         for writer in other_writers:
            print(f"            - {writer} writes {var} in lines {[n.lineno for n in writers[writer]]}")
      if other_readers or other_writers:
         print()
         
   def _print_no_shared(self, target) -> None:
      print(f"\n Thread routine: {target.name}")
      print(f"   No shared state detected")

   def _analyze_calls(self, target, unprotected_calls) -> bool:
      found_bad_call = False
      tname = target.parent_target if target.parent_target is not None else target.name
      scope = self.model.function_scopes.get(tname)
      for func, nodes in target.calls.items():
         if func not in unprotected_calls:
            continue

         receiver = self._get_receiver(func)

         # If we can positively identify the receiver as local/unshared, skip it
         if receiver is not None and not self._receiver_is_shared(receiver, scope):
            continue

         for node in nodes:
            if not self._is_protected(node): 
               found_bad_call = True
               if self.mode == "verbose":
                  print(f"      Unprotected call  of {func} in line {node.lineno}")

      return found_bad_call
         
   def _analyze_shared_variables(self, target, shared_reads, shared_writes) -> set[str]:
      violations = set()
      tname = target.parent_target if target.parent_target is not None else target.name
      
      for var in shared_reads:
         shared_info = self.model.shared_vars.get(var)
         if not shared_info:
            continue
                     
         readers = shared_info["reads"]
         writers = shared_info["writes"]

         for node in readers.get(f"{tname}", []):
            if not self._is_node_protected(node, target):
               var_type = self.model.var_types.get(var)
               match var_type:
                  case "list": kind = "SHARED LIST"
                  case "set":  kind = "SHARED SET"
                  case "dict": kind = "SHARED DICT"
                  case _:      kind = "SC"
               violations.add(kind)
               
               if self.mode == "verbose":
                  print(f"      Unprotected read of {var} in line {node.lineno} [{kind}]")

         if violations and self.mode == "verbose":
            self._print_other_threads(var, tname, readers, writers)

      for var in shared_writes:
         shared_info = self.model.shared_vars.get(var)
         if not shared_info:
            continue
                     
         readers = shared_info["reads"]
         writers = shared_info["writes"]

         if tname in writers:
            for node in writers.get(tname, []):
               if not self._is_node_protected(node, target):
                  var_type = self.model.var_types.get(var)
                  match var_type:
                     case "list": kind = "SHARED LIST"
                     case "set":  kind = "SHARED SET"
                     case "dict": kind = "SHARED DICT"
                     case _:      kind = "SC"
                  violations.add(kind)
                  if self.mode == "verbose":
                     print(f"      Unprotected write of {var} in line {node.lineno} [{kind}]")

               if self.mode == "verbose":
                  self._print_other_threads(var, tname, readers, writers)

      return violations
 
   def analyze_shared_vars(self) -> dict: # json
      if not self.model.thread_targets:
         return {"program": self.model.name, "unsafe": False,
                "violations": [], "shared_vars": {}, "thread_targets": []}

      if self.mode == "verbose":
         print(f"\nAnalyzing threads in program: {self.model.name}")
      
      all_violations = set()
      found_bad_call = False
      
      for target in self.model.thread_targets:
         shared_reads, shared_writes = self._get_shared_accesses(target)
         unprotected_calls = self._get_unprotected_calls(target)

         if shared_reads or shared_writes or unprotected_calls:
            if self.mode == "verbose":
               self._print_thread_header(target, shared_reads, shared_writes, unprotected_calls)

            violations = self._analyze_shared_variables(target, shared_reads, shared_writes)
            all_violations |= violations
            found_bad_call = self._analyze_calls(target, unprotected_calls) or found_bad_call

            if self.mode == "verbose":
               if not violations:
                  print("      No unprotected variables detected") 
               if not found_bad_call:
                  print("      No unprotected function calls detected")
         else:
            if self.mode == "verbose":
               self._print_no_shared(target)

      result = self.to_json(all_violations, found_bad_call)
            
      return result
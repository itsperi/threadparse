import ast
from model import ThreadTarget, Scope
from collections import defaultdict

COLLECTION_CONSTRUCTORS = {"list", "dict", "set", "defaultdict", "OrderedDict", "Counter"}

'''
Custom NodeVisitor that enables parent tracking for upwards recursion
'''
class PCNodeVisitor(ast.NodeVisitor):
    def generic_visit(self, node):
        for child in ast.iter_child_nodes(node):
            child.parent = node
            self.visit(child)

'''
This pass of the AST is meant to detect
whether a file contains the threading import
'''

class ImportDetection(ast.NodeVisitor):
   def __init__(self):
      self.uses_threading = False

   def visit_Import(self, node):
      for alias in node.names:
         if alias.name in {"threading",
                           "_thread",
                           "concurrent.futures",
                           "multiprocessing.pool"}:
            self.uses_threading = True
            return
            
      self.generic_visit(node)

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
            
'''
This pass of the AST is meant to gather the names and locations
of global, nonlocal variables and function definitions to later 
use as potential pieces of interest in threads, as well as 
gathering info about class definitions for later resolution of method calls
'''
class SymbolPass(PCNodeVisitor):
   def __init__(self, model):
      self.model = model
      self.current_function = None
      self.current_class = None
      
   # Simply used to update the live class we're visiting
   def visit_ClassDef(self, node):
      previous_class = self.current_class
      self.current_class = node.name
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

   def visit_Global(self, node):
      for name in node.names:
         self.model.globals[name] = node
         
         if self.current_function:
            self.model.function_globals \
               .setdefault(self.current_function, set()) \
               .add(name)

   def visit_Nonlocal(self, node):
      for name in node.names:
         self.model.nonlocals[name] = node
         
         if self.current_function:
            self.model.function_nonlocals \
               .setdefault(self.current_function, set()) \
               .add(name)
               
   def visit_Assign(self, node):
      if self.current_function is None:
         for target in node.targets:
            for name in self._extract_names(target):
               self.model.module_vars[name] = node
      self.generic_visit(node)
      
   def visit_With(self, node):
      for with_item in node.items:
         context_expr = with_item.context_expr
         if isinstance(context_expr, ast.Call):
            func = context_expr.func
            is_executor = (
               isinstance(func, ast.Name) and func.id == "ThreadPoolExecutor"
            ) or (
               isinstance(func, ast.Attribute) and func.attr == "ThreadPoolExecutor"
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

'''
This pass of the AST is meant to gather types of
variables created in the program so we can filter
out the mutations to Python's naturally unsafe default
collections like lists, sets, and dicts (Queues are threadsafe tho)
'''
class TypeInferencePass(PCNodeVisitor):
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
      prev = self.current_function
      self.current_function = node.name
      self.generic_visit(node)
      self.current_function = prev

   def visit_Assign(self, node):
      inferred = self._infer(node.value)
      if inferred:
         for target in node.targets:
            for name in self._extract_names(target):
               self.model.var_types[name] = inferred
      self.generic_visit(node)

   def visit_AnnAssign(self, node):
      # Handles: x: list = []
      inferred = self._infer(node.value) if node.value else self._infer_annotation(node.annotation)
      if inferred and isinstance(node.target, ast.Name):
         self.model.var_types[node.target.id] = inferred
      self.generic_visit(node)

   def _infer(self, node):
      if node is None:
         return None
      if isinstance(node, ast.List):
         return "list"
      if isinstance(node, ast.Dict):
         return "dict"
      if isinstance(node, ast.Set):
         return "set"
      # Constructor calls: list(), dict(), set(), defaultdict(list), etc.
      if isinstance(node, ast.Call):
         return self._infer_call(node)
      return None

   def _infer_call(self, node):
      if isinstance(node.func, ast.Name):
         name = node.func.id
         if name in COLLECTION_CONSTRUCTORS:
            # Normalize defaultdict/OrderedDict()
            return {"defaultdict": "dict", "OrderedDict": "dict",
                  "Counter": "dict"}.get(name, name)
      elif isinstance(node.func, ast.Attribute):
         # Normalize collections.defaultdict(...)
         if node.func.attr in COLLECTION_CONSTRUCTORS:
            return {"defaultdict": "dict", "OrderedDict": "dict",
                  "Counter": "dict"}.get(node.func.attr, node.func.attr)
      return None

   def _infer_annotation(self, node):
      # Handles bare annotations like x: list
      if isinstance(node, ast.Name) and node.id in {"list", "dict", "set"}:
         return node.id
      # Handles subscript annotations like x: list[int]
      if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
         if node.value.id in {"List", "Dict", "Set", "list", "dict", "set"}:
            return node.value.id.lower()
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

'''
This pass of the AST is meant to go over the function
definitions, variable writes in each function/scope
and define the stack scope for each
'''
class ScopePass(PCNodeVisitor):
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
   and add the parameters as local variables. When we exit, we pop the scope
   and save it in the program model for later use.
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
         
'''
This pass of the AST is meant to gather 
info about which functions are designated 
as Thread targets and saves their nodes for later use
'''
class ThreadPass(PCNodeVisitor):
   def __init__(self, model):
      self.model = model
      self.current_class = None
      
   def visit_ClassDef(self, node):
      previous = self.current_class
      self.current_class = node.name
      self.generic_visit(node)
      self.current_class = previous
      
   def _check_threading(self, node, kw):
      if kw.arg == "target":
         name = self._qualify(kw.value)
         # Only targets known as defined functions are added
         if name and name not in self.model.seen_targets \
         and name in self.model.functions.keys():
            target = ThreadTarget(name, self.current_class, node)
            self.model.thread_targets.append(target)
            self.model.seen_targets.add(name)
   
   def _check_thread(self, arg):
      # The first argument to start_new_thread() is the target function
      name = self._qualify(arg)         
      if name and name not in self.model.seen_targets \
      and name in self.model.functions.keys():
         target = ThreadTarget(name, self.current_class, arg)
         self.model.thread_targets.append(target)
         self.model.seen_targets.add(name)
         
   def _check_executor(self, arg, executor_node=None):
      name = self._qualify(arg)
      if name and name not in self.model.seen_targets \
      and name in self.model.functions.keys():
         # Check if the call is on a known executor
         executor_name = self._qualify(executor_node) if executor_node else None
         if executor_name and executor_name in self.model.executors:
               target = ThreadTarget(name, self.current_class, arg)
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
               
         # or for _threading.start_new_thread cases
         elif node.func.attr == "start_new_thread":
            if node.args:
               self._check_thread(node.args[0]) 
         
         elif node.func.attr == "submit" or node.func.attr == "map":
            if node.args:
               self._check_executor(node.args[0], node.func.value)

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
               return f"{_class}.{method}"
         return method

      if isinstance(node, ast.Name):
         return node.id

      return None
   
'''
This pass of the AST is meant to 
build a call graph so we can 
find transitive threadtarget calls later
'''
class CallGraphPass(PCNodeVisitor):
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

   def visit_Call(self, node):
      if not self.current_function:
         return

      callee = None

      if isinstance(node.func, ast.Name):
         callee = node.func.id

      elif isinstance(node.func, ast.Attribute):
         callee = node.func.attr

      if callee and callee in self.model.functions:
         self.model.call_graph[self.current_function].add(callee)

      self.generic_visit(node)

'''
This class is meant to update the
program model so that we include 
the indirect thread targets
'''
class ThreadExpansion:
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
                    self.model.thread_targets.append(
                        ThreadTarget(callee, None, self.model.functions[callee])
                    )
                    

MUTATING_METHODS = {
   "append", "extend", "insert", "remove",
   "pop", "clear", "add", "discard",
   "push", "enqueue", "dequeue",
}

'''
This pass of the AST is meant to target specifically the 
functions designated as Thread targets and gather information
about the variable reads, writes, function calls with 
potential side effects, and subscript/attribute accesses 
'''
class TargetPass(PCNodeVisitor):
   def __init__(self, scope: Scope = None, class_name: str = None, var_types = None):
      self.reads = defaultdict(list)
      self.writes = defaultdict(list)
      self.calls = defaultdict(list)
      self.scope = scope
      self.class_name = class_name
      self.var_types = var_types
      
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
      # Let's avoid double counting
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
               
      # Don't call generic_visit, we'll double count
      # self.generic_visit(node)     
      
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
      # Case 1: func()
      if isinstance(node.func, ast.Name):
         func_name = node.func.id
         if func_name in MUTATING_METHODS:
            self.calls[func_name].append(node)

      # Case 2: obj.method()
      elif isinstance(node.func, ast.Attribute):
         func_name = self.get_full_attr_name(node.func)
         if func_name and func_name.startswith("self.") and self.class_name:
            func_name = f"{self.class_name}.{func_name[5:]}"
         method = node.func.attr
         obj = node.func.value

         if method in MUTATING_METHODS and isinstance(obj, ast.Name):
            if isinstance(obj, ast.Name):
               obj_type = self.var_types.get(obj.id)
               if obj_type in {"list", "set", "dict"} or obj_type is None: 
                  self.calls[func_name].append(node)

      self.generic_visit(node)
      
   # For subscript writes
   def visit_Subscript(self, node):
      if isinstance(node.ctx, ast.Store):
         if isinstance(node.value, ast.Name):
            self.writes[node.value.id].append(node)
            
      elif isinstance(node.ctx, ast.Load):
         if isinstance(node.value, ast.Name):
            parent = getattr(node, "parent", None)
            if not isinstance(parent, ast.Assign):
               self.reads[node.value.id].append(node)
      self.generic_visit(node)


'''
This class is meant to update the program model
with the relevant data collected from the thread pass
'''
class TargetUpdate:
   def __init__(self, model):
      self.model = model
      
   def update_thread_accesses(self):
      # if not self.model.thread_targets:
         # print("No thread targets found...")
      for target in self.model.thread_targets:
         target_node = self.model.functions[target.name]
         target_scope = self.model.function_scopes.get(target.name)
         target_class = target.class_name
         
         parser = TargetPass(scope = target_scope, 
                             class_name = target_class,
                             var_types = self.model.var_types)
         parser.visit(target_node)
         
         target.reads = parser.reads
         target.writes = parser.writes
         target.calls = parser.calls

'''
This pass of the AST is meant to check 
targets belonging to a class and capture
info about instance attributes
'''
class ClassResolutionPass(PCNodeVisitor):
   def __init__(self, model):
      self.model = model

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

            method_node = self.model.functions.get(qualified)
            if not method_node:
               continue

            scope = self.model.function_scopes.get(qualified)
            parser = TargetPass(scope=scope)
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
                        
'''
This pass of the AST is meant to analyze
variables/data structures across the entire
program and detect any that are shared among thread targets
'''
class SharedUpdate(PCNodeVisitor):
   def __init__(self, model):
      self.model = model
      # We should store the nodes of the reads/writes 
      # in the form: var -> target -> list of nodes for easier analysis later
      self.var_reads: dict[str, dict[str, list[ast.AST]]] = defaultdict(lambda: defaultdict(list))
      self.var_writes: dict[str, dict[str, list[ast.AST]]] = defaultdict(lambda: defaultdict(list))
      # May God smite my children for what I just wrote
      
   def _populate(self):
      # In each target, we have dicts of reads/writes: var -> list of nodes
      target_names = {t.name for t in self.model.thread_targets}
      
      for target in self.model.thread_targets:
         tname = target.name         
         for var, nodes in target.reads.items():
            self.var_reads[var][tname].extend(nodes)
            
         for var, nodes in target.writes.items():
            self.var_writes[var][tname].extend(nodes)
            
         for var, method_nodes in target.sibling_writes.items():
            for method, nodes in method_nodes.items():
               # Key by "ClassName.method" so the source is identifiable
               sibling_key = f"{tname}::{method}"
               self.var_writes[var][sibling_key].extend(nodes)
               
      # We need to consider main thread scope
      for fname, fnode in self.model.functions.items():
         if fname in target_names:
               continue
         scope = self.model.function_scopes.get(fname)
         class_name = self.model.method_to_class.get(fname)
         parser = TargetPass(scope=scope, 
                              class_name=class_name,
                              var_types=self.model.var_types)
         parser.visit(fnode)

         for var, nodes in parser.writes.items():
               self.var_writes[var][f"__main__::{fname}"].extend(nodes)
         for var, nodes in parser.reads.items():
               self.var_reads[var][f"__main__::{fname}"].extend(nodes)

   def _update(self):
      shared = {}
      all_vars = set(self.var_reads.keys()) | set(self.var_writes.keys())
      
      for var in all_vars:
         reader_targets = set(self.var_reads[var].keys())
         writer_targets = set(self.var_writes[var].keys())
         involved_targets = reader_targets | writer_targets
         
         # We should include the nodes themselves
         if len(involved_targets) > 1 and len(writer_targets) > 0:
            shared[var] = {
               "reads": {t: self.var_reads[var][t] for t in reader_targets},
               "writes": {t: self.var_writes[var][t] for t in writer_targets}
            }
            
      self.model.shared_vars = shared
   
   def update_shared_vars(self):
      self._populate()
      self._update()

'''
This pass of the AST is meant to go over the thread targets
identified earlier along with the relevant information
from the previous passes and detect novel behavior that
may be considered thread-unsafe
'''
class CriticalPass:
   def __init__(self, model):
      self.model = model
      
   def _is_with_locking(self, node):
      locks = ("lock", "acquire")
      for item in node.items:
         ctx = item.context_expr
         
         if isinstance(ctx, ast.Name):
            for lock in locks:
               if lock in ctx.id.lower():
                  return True
                  
         if isinstance(ctx, ast.Attribute):
            for lock in locks:
               if lock in ctx.attr.lower():
                  return True
                  
      return False
      
   def _is_inside_with(self, node):
      current = node
      while hasattr(current, "parent") and current.parent:
         if isinstance(current.parent, ast.With):
            if self._is_with_locking(current.parent):
               return True
         current = current.parent
      return False     
   
   def _is_lock_call(self, node):
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

   def _is_unlock_call(self, node):
      if not isinstance(node, ast.Expr):
         return False

      call = node.value
      if not isinstance(call, ast.Call):
         return False

      if isinstance(call.func, ast.Attribute):
         return call.func.attr.lower() in ("unlock", "release")

      return False
   
   def _get_parent_statement(self, node):
      current = node
      while current and not isinstance(current, ast.stmt):
         current = getattr(current, "parent", None)
      return current
   
   def _is_between_lock_unlock(self, node):
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

         if lock_found:
            # search forward for unlock
            for i in range(idx + 1, len(body)):
               if self._is_lock_call(body[i]):
                  break
               if self._is_unlock_call(body[i]):
                  return True
         current = parent
   
      return False
   
   def _is_protected(self, node):
      return self._is_inside_with(node) or self._is_between_lock_unlock(node)
   
   def _is_external(self, name, scope: Scope):
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
   
   def _is_shared_variable(self, var, scope: Scope):
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
   
   def _get_shared_accesses(self, target):
      shared_reads = {
         var for var in target.reads
         if self._is_shared_variable(var, scope=self.model.function_scopes.get(target.name))
      }

      shared_writes = {
         var for var in target.writes
         if self._is_shared_variable(var, scope=self.model.function_scopes.get(target.name))
      }

      return shared_reads, shared_writes
   
   def _classify_variable(self, var):
      if var in self.model.globals:
         return "GLOBAL"
      if var in self.model.nonlocals:
         return "NONLOCAL"
      if var in self.model.shared_vars:
         return "CROSS_THREAD"
      if var in self.model.module_vars:
         return "MAIN_SCOPE"
      return "OTHER"
   
   def _get_unprotected_calls(self, target):
      return {func for func in target.calls if not self._is_protected(func)}
   
   def _print_thread_header(self, target, shared_reads, shared_writes, calls):
      print(f"\nThread routine: {target.name}")
      
      # if shared_reads:
      #    print("   Reads shared variables:")
      #    for var in shared_reads:
      #       print(f"    {var} [{self._classify_variable(var)}]")
      #    print() 
            
      if shared_writes:
         print("   Writes shared variables:")
         for var in shared_writes:
            print(f"    {var} [{self._classify_variable(var)}]")
         print() 

      if calls:
         print("   Calls functions:", calls)
         
   def _print_other_threads(self, var, current, readers, writers):
      other_readers = [t for t in readers if t != current]
      other_writers = [t for t in writers if t != current]

      if other_readers:
         print(f"      Other threads reading {var}: {other_readers}")
      if other_writers:
         print(f"      Other threads writing {var}: {other_writers}")
      if other_readers or other_writers:
         print()
         
   def _print_no_shared(self, target):
      print(f"\n Thread routine: {target.name}")
      print(f"   No shared state detected")

   def _analyze_calls(self, target, unprotected_calls):
      found_bad_call = False
      
      scope = self.model.function_scopes.get(target.name)
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
                  print(f"      Unprotected call  of {func} in line {node.lineno}")

      # for func, nodes in target.calls.items():
      #    for node in nodes:
      #          if not self._is_protected(node):
      #             if func in unprotected_calls:
      #                print(f"      Unprotected call  to {func} in line {node.lineno}")
      #                found_bad_call = True

      return found_bad_call
         
   def _analyze_shared_variables(self, target, shared_reads, shared_writes):
      found_bad_var = False
      
      # for var in shared_reads:
      #    shared_info = self.model.shared_vars.get(var)
      #    if not shared_info:
      #       for node in target.reads.get(var, []):
      #          if not self._is_protected(node):
      #             found_bad_var = True
      #             print(f"      Unprotected read of {var} in line {node.lineno}")
      #       continue
                  
      #    readers = shared_info["reads"]
      #    writers = shared_info["writes"]

      #    for node in readers[target.name]:
      #       if not self._is_protected(node):
      #          found_bad_var = True
      #          print(f"      Unprotected read of {var} in line {node.lineno}")

      #    self._print_other_threads(var, target.name, readers, writers)

      for var in shared_writes:
         shared_info = self.model.shared_vars.get(var)
         if not shared_info:
            for node in target.writes.get(var, []):
               if not self._is_protected(node):
                  found_bad_var = True
                  print(f"      Unprotected write of {var} in line {node.lineno}")
            continue
                  
         readers = shared_info["reads"]
         writers = shared_info["writes"]

         if target.name in writers:
               for node in writers[target.name]:
                  if not self._is_protected(node):
                     found_bad_var = True
                     print(f"      Unprotected write of {var} in line {node.lineno}")

               self._print_other_threads(var, target.name, readers, writers)
               
      return found_bad_var
 
   def analyze_shared_vars(self):
      if not self.model.thread_targets:
         return
      
      print(f"\nAnalyzing threads in program: {self.model.name}")
      
      found_bad_var, found_bad_call = False, False
      
      for target in self.model.thread_targets:
         shared_reads, shared_writes = self._get_shared_accesses(target)
         unprotected_calls = self._get_unprotected_calls(target)

         if shared_reads or shared_writes or unprotected_calls:
            self._print_thread_header(target, shared_reads, shared_writes, unprotected_calls)

            found_bad_var = self._analyze_shared_variables(target, shared_reads, shared_writes) or found_bad_var
            found_bad_call = self._analyze_calls(target, unprotected_calls) or found_bad_call

            if not found_bad_var:
               print("      No unprotected variables detected")
            
            if not found_bad_call:
               print("      No unprotected function calls detected")
               
         else:
            self._print_no_shared(target)

      return {
               "unsafe": found_bad_var or found_bad_call
            }  
                  
         
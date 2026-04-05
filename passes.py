import ast
from model import ThreadTarget, Scope
from collections import defaultdict

'''
Custom NodeVisitor that enables parent tracking for upwards recursion
'''
class PCNodeVisitor(ast.NodeVisitor):
    def generic_visit(self, node):
        for child in ast.iter_child_nodes(node):
            child.parent = node
            self.visit(child)
            
'''
This pass of the AST is meant to gather the names and locations
of global, nonlocal variables and function definitions to later 
use as potential pieces of interest in threads
'''
class SymbolPass(PCNodeVisitor):
   def __init__(self, model):
      self.model = model
      self._current_function = None

   def visit_FunctionDef(self, node):
      self.model.functions[node.name] = node
      previous = self._current_function
      self._current_function = node.name
      self.generic_visit(node)
      self._current_function = previous

   def visit_Global(self, node):
      for name in node.names:
         self.model.globals[name] = node
         
         if self._current_function:
            self.model.function_globals \
               .setdefault(self._current_function, set()) \
               .add(name)

   def visit_Nonlocal(self, node):
      for name in node.names:
         self.model.nonlocals[name] = node
         
         if self._current_function:
            self.model.function_nonlocals \
               .setdefault(self._current_function, set()) \
               .add(name)
               
   def visit_Assign(self, node):
      if self._current_function is None:
         for target in node.targets:
            for name in self._extract_names(target):
               self.model.module_vars[name] = node
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
This pass of the AST is meant to go over the function
definitions, variable writes in each function/scope
and define the stack scope for each
'''
class ScopePass(PCNodeVisitor):
   def __init__(self, model):
      self.model = model
      self.scope_stack: list[Scope] = []

   def current_scope(self):
      return self.scope_stack[-1] if self.scope_stack else None

   '''
   Whenever we enter a function definition, we create a new scope
   and add the parameters as local variables. When we exit, we pop the scope
   and save it in the program model for later use.
   '''
   def visit_FunctionDef(self, node):
      scope = Scope(parent=self.current_scope())
      self.scope_stack.append(scope)

      # parameters are locals
      for arg in node.args.args:
         scope.locals.add(arg.arg)

      self.generic_visit(node)

      self.model.function_scopes[node.name] = scope
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
      
   def visit_Call(self, node):
      if isinstance(node.func, ast.Name):
         if node.func.id == "Thread":
            for kw in node.keywords:
               if kw.arg == "target":
                  name : str | None = self.get_target(kw.value)
                  if name and name not in self.model.seen_targets and name in self.model.functions.keys():
                     target = ThreadTarget(name, node)
                     self.model.thread_targets.append(target)
                     self.model.seen_targets.add(name)
                     # print(f"Found thread target: {target.name} {(node.lineno, node.col_offset)}")

      elif isinstance(node.func, ast.Attribute):
         if node.func.attr == "Thread":
            for kw in node.keywords:
               if kw.arg == "target":
                  name = self.get_target(kw.value)
                  if name and name not in self.model.seen_targets and name in self.model.functions.keys():
                     target = ThreadTarget(name, node)
                     self.model.thread_targets.append(target)
                     self.model.seen_targets.add(name)
                     # print(f"Found thread target: {target.name} {(node.lineno, node.col_offset)}")
                     
      self.generic_visit(node)
                     
   def get_target(self, node):
      if isinstance(node, (ast.Name, ast.Attribute)):
         if isinstance(node, ast.Name):
            return node.id
         if isinstance(node, ast.Attribute):
            return node.attr
      return None
   
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
   def __init__(self, scope: Scope = None):
      self.reads = defaultdict(list)
      self.writes = defaultdict(list)
      self.calls = defaultdict(list)
      self.scope = scope
      
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

      # Case 1: simple variable (x += 1)
      if isinstance(target, ast.Name):
         self.reads[target.id].append(node)
         self.writes[target.id].append(node)

      # Case 2: attribute (self.x += 1)
      elif isinstance(target, ast.Attribute):
         full_name = self.get_full_attr_name(target)
         if full_name:
               self.reads[full_name].append(node)
               self.writes[full_name].append(node)

      # Case 3: subscript (arr[i] += 1)
      elif isinstance(target, ast.Subscript):
         if isinstance(target.value, ast.Name):
               self.reads[target.value.id].append(node)
               self.writes[target.value.id].append(node)
               
      # Don't call generic_visit, we'll double count
      # self.generic_visit(node)     
      
   # For attribute accesses like class.x
   def visit_Attribute(self, node):
      full_name = self.get_full_attr_name(node)

      if full_name:
         # If this attribute is part of a Call, classify ONLY as call, not read
         if isinstance(node.parent, ast.Call) and node.parent.func is node:
               return
            
         root = full_name.split(".")[0]
         
         if self.scope and root in self.scope.locals and root not in {"self", "cls"}:
            # If the root of this attribute is a local variable, we can ignore it for cross-thread sharing purposes
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
            self.writes[func_name].append(node)

      # Case 2: obj.method()
      elif isinstance(node.func, ast.Attribute):
         func_name = self.get_full_attr_name(node.func)
         method = node.func.attr
         obj = node.func.value

         if method in MUTATING_METHODS:
            if isinstance(obj, ast.Name):
               self.writes[obj.id].append(node)

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
         
         parser = TargetPass(scope = target_scope)
         parser.visit(target_node)
         
         target.reads = parser.reads
         target.writes = parser.writes
         target.calls = parser.calls
         
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
      
      
   def _populate(self):
      # In each target, we have dicts of reads/writes: var -> list of nodes
      for target in self.model.thread_targets:
         tname = target.name         
         for var, nodes in target.reads.items():
            self.var_reads[var][tname].extend(nodes)
            
         for var, nodes in target.writes.items():
            self.var_writes[var][tname].extend(nodes)

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
identified earlier along with their state and detect any
behavior that may be considered thread-unsafe
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
      
      return self._is_external(var, scope)
   
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
      return {func for func in target.calls}
   
   def _print_thread_header(self, target, shared_reads, shared_writes, calls):
      print(f"\nThread routine: {target.name}")
      
      if shared_reads:
         print("   Reads shared variables:")
         for var in shared_reads:
            print(f"    {var} [{self._classify_variable(var)}]")
         print() 
            
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

      for func, nodes in target.calls.items():
         for node in nodes:
               if not self._is_protected(node):
                  if func in unprotected_calls:
                     print(f"      Unprotected call  to {func} in line {node.lineno}")
                     found_bad_call = True

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
      
      print(f"\nAnalyzing program: {self.model.name}")
      
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
                  
         
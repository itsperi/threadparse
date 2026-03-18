import ast
from model import ThreadTarget, Scope
from collections import defaultdict

# Custom NodeVisitor that enables parent tracking for upwards recursion
class PCNodeVisitor(ast.NodeVisitor):
    def generic_visit(self, node):
        for child in ast.iter_child_nodes(node):
            child.parent = node
            self.visit(child)
            
class SymbolPass(PCNodeVisitor):
   def __init__(self, model):
      self.model = model
      
   def visit_FunctionDef(self, node):
      self.model.functions[node.name] = node
      self.generic_visit(node)
      
   def visit_Global(self, node):
      for name in node.names:
         self.model.globals[name] = node
      self.generic_visit(node)
         
   def visit_Nonlocal(self, node):
      for name in node.names:
         self.model.nonlocals[name] = node
      self.generic_visit(node)
      
class ScopePass(PCNodeVisitor):
   def __init__(self, model):
      self.model = model
      self.scope_stack = []

   def current_scope(self):
      return self.scope_stack[-1] if self.scope_stack else None

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
         scope.locals.add(node.id)

   def visit_Global(self, node):
      scope = self.current_scope()
      if scope:
         scope.globals.update(node.names)

   def visit_Nonlocal(self, node):
      scope = self.current_scope()
      if scope:
         scope.nonlocals.update(node.names)
         
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
                     print(f"Found thread target: {target.name} {(node.lineno, node.col_offset)}")

      elif isinstance(node.func, ast.Attribute):
         if node.func.attr == "Thread":
            for kw in node.keywords:
               if kw.arg == "target":
                  name = self.get_target(kw.value)
                  if name and name not in self.model.seen_targets and name in self.model.functions.keys():
                     target = ThreadTarget(name, node)
                     self.model.thread_targets.append(target)
                     self.model.seen_targets.add(name)
                     print(f"Found thread target: {target.name} {(node.lineno, node.col_offset)}")
                     
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
   "pop", "clear", "update", "add", "discard"
}

class TargetPass(PCNodeVisitor):
   def __init__(self):
      self.reads = defaultdict(list)
      self.writes = defaultdict(list)
      
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
      if isinstance(node.ctx, (ast.Load, ast.Store)):
         if isinstance(node.ctx, ast.Load):
            self.reads[node.id].append(node)
         if isinstance(node.ctx, ast.Store):
            self.writes[node.id].append(node)
      self.generic_visit(node)
   
   # For shorthand exprs like x += 1
   def visit_AugAssign(self, node):
      if isinstance(node.target, ast.Name):
         self.reads[node.target.id].append(node)
         self.writes[node.target.id].append(node)
      # Don't call generic_visit, we'll double count
      # self.generic_visit(node)     
      
   # For attribute accesses like class.x
   def visit_Attribute(self, node):
      full_name = self.get_full_attr_name(node)

      if full_name:
         if isinstance(node.ctx, ast.Load):
            self.reads[full_name].append(node)
         elif isinstance(node.ctx, ast.Store):
            self.writes[full_name].append(node)

      self.generic_visit(node)
      
   # For method calls that may be
   # mutating a shared data structure
   def visit_Call(self, node):
      if isinstance(node.func, ast.Attribute):
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
      self.generic_visit(node)

class TargetUpdate:
   def __init__(self, model):
      self.model = model
      
   def update_thread_accesses(self):
      if not self.model.thread_targets:
         print("No thread targets found...")
      for target in self.model.thread_targets:
         target_node = self.model.functions[target.name]
         
         parser = TargetPass()
         parser.visit(target_node)
         
         target.reads = parser.reads
         target.writes = parser.writes
            
class CriticalPass:
   def __init__(self, model):
      self.model = model
      
   def is_with_locking(self, node):
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
      
   def is_inside_with(self, node):
      current = node
      while hasattr(current, "parent") and current.parent:
         if isinstance(current.parent, ast.With):
            if self.is_with_locking(current.parent):
               return True
         current = current.parent
      return False     
   
   def is_lock_call(self, node):
      if not isinstance(node, ast.Expr):
         return False

      call = node.value
      if not isinstance(call, ast.Call):
         return False

      if isinstance(call.func, ast.Attribute):
         return call.func.attr.lower() in ("lock", "acquire")

      return False

   def is_unlock_call(self, node):
      if not isinstance(node, ast.Expr):
         return False

      call = node.value
      if not isinstance(call, ast.Call):
         return False

      if isinstance(call.func, ast.Attribute):
         return call.func.attr.lower() in ("unlock", "release")

      return False
   
   def get_parent_statement(self, node):
      current = node
      while current and not isinstance(current, ast.stmt):
         current = getattr(current, "parent", None)
      return current
   
   def is_between_lock_unlock(self, node):
      stmt = self.get_parent_statement(node)

      parent = stmt.parent
      if not hasattr(parent, "body"):
         return False

      body = parent.body
      if stmt not in body:
         return False
      idx = body.index(stmt)
      lock_found = False

      # search backwards for lock
      for i in range(idx - 1, -1, -1):
         if self.is_unlock_call(body[i]):
            break
         if self.is_lock_call(body[i]):
            lock_found = True
            break

      if not lock_found:
         return False

      # search forward for unlock
      for i in range(idx + 1, len(body)):
         if self.is_lock_call(body[i]):
            break
         if self.is_unlock_call(body[i]):
            return True

      return False
   
   def is_external(self, name, scope: Scope):
      if name in scope.locals:
         return False

      if name in scope.globals or name in scope.nonlocals:
         return True

      parent = scope.parent
      while parent:
         if name in parent.locals:
            return True
         parent = parent.parent

      return True  # assume module/global
   
   def analyze_shared_vars(self):
      if not self.model.thread_targets:
         return
      # g_vars = set(self.model.globals.keys())
      # nl_vars = set(self.model.nonlocals.keys())
      
      for target in self.model.thread_targets:
         scope = self.model.function_scopes[target.name]
         
         # shared_reads = set(target.reads.keys() & (g_vars | nl_vars))
         # shared_writes = set(target.writes.keys() & (g_vars | nl_vars))

         shared_reads = {
            var for var in target.reads
            if self.is_external(var, scope)
         }

         shared_writes = {
            var for var in target.writes
            if self.is_external(var, scope)
         }
         
         if shared_reads or shared_writes:
            print(f"\nThread routine: {target.name}")
            if shared_reads:
               print("   Reads shared variables:", shared_reads)
            if shared_writes:
               print("   Writes shared variables:", shared_writes)
            
            found_bad_var = False
            for var, nodes in target.writes.items():
               for node in nodes:
                  if not (self.is_inside_with(node) or self.is_between_lock_unlock(node)) and var in shared_writes:
                        found_bad_var = True
                        print(f"      Unprotected write of {var} in line {node.lineno}")   
   
            if not found_bad_var:
               print("      No unprotected variables detected")
         else:
            print(f"\n Thread routine: {target.name}")
            print(f"   No shared variables detected")

                  
         
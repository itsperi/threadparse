import ast
from model import ThreadTarget
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
         
class ThreadPass(PCNodeVisitor):
   def __init__(self, model):
      self.model = model
      
   def visit_Call(self, node):
      if isinstance(node.func, ast.Name):
         if node.func.id == "Thread":
            for kw in node.keywords:
               if kw.arg == "target":
                  name : str | None = self.get_target(kw.value)
                  if name:
                     target = ThreadTarget(name, node)
                     self.model.thread_targets.append(target)
                     print(f"Found thread target: {target.name}")

      elif isinstance(node.func, ast.Attribute):
         if node.func.attr == "Thread":
            for kw in node.keywords:
               if kw.arg == "target":
                  name = self.get_target(kw.value)
                  if name:
                     target = ThreadTarget(name, node)
                     self.model.thread_targets.append(target)
                     print(f"Found thread target: {target.name}")
                     
      self.generic_visit(node)
                     
   def get_target(self, node):
      if isinstance(node, (ast.Name, ast.Attribute)):
         if isinstance(node, ast.Name):
            return node.id
         if isinstance(node, ast.Attribute):
            return node.attr
      return None
   
class TargetPass(PCNodeVisitor):
   def __init__(self):
      self.reads = defaultdict(list)
      self.writes = defaultdict(list)
      
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
         self.writes[node.target.id].append(node)
      self.generic_visit(node)     
      
   # For attribute accesses like class.x
   def visit_Attribute(self, node):
      if isinstance(node.ctx, ast.Load):
         self.reads[node.attr].append(node)
      elif isinstance(node.ctx, ast.Store):
         self.writes[node.attr].append(node)
      self.generic_visit(node)

class TargetUpdate:
   def __init__(self, model):
      self.model = model
      
   def update(self):
      for target in self.model.thread_targets:
         target_node = self.model.functions[target.name]
         
         parser = TargetPass()
         parser.visit(target_node)
         
         target.reads = parser.reads
         target.writes = parser.writes
            
class CriticalPass:
   def __init__(self, model):
      self.model = model
      
   def analyze(self):
      g_vars = set(self.model.globals.keys())
      nl_vars = set(self.model.nonlocals.keys())
      
      for target in self.model.thread_targets:
         shared_reads = set(target.reads.keys() & (g_vars | nl_vars))
         shared_writes = set(target.writes.keys() & (g_vars | nl_vars))

         if shared_reads or shared_writes:
            print(f"\nThread routine: {target.name}")
            if shared_reads:
               print("  Reads shared variables:", shared_reads)
            if shared_writes:
               print("  Writes shared variables:", shared_writes)
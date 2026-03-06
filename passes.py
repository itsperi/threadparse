import ast
from model import ThreadTarget

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
                  name = self.get_target(kw.value)
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
         match node.__class__:
            case ast.Name:
               return node.id
            case ast.Attribute:
               return node.attr
      return None
   
class TargetPass(PCNodeVisitor):
   def __init__(self, model):
      self.reads = set()
      self.writes = set()
      self.model = model
      
   # For reads/writes within thread targets
   def visit_Name(self, node):
      if isinstance(node.ctx, (ast.Load, ast.Store)):
         match node.ctx:
            case ast.Load:
               self.reads.add(node.id)
            case ast.Store:
               self.writes.add(node.id)
      self.generic_visit(node)
   
   # For shorthand exprs like x += 1
   def visit_AugAssign(self, node):
      if isinstance(node.target, ast.Name):
         self.writes.add(node.target.id)
      self.generic_visit(node)     

   def update(self):
      for target in self.model.thread_targets:
         if target.name in self.model.functions:
            target_node = self.model.functions[target.name]
            
            parser = TargetPass()
            parser.visit(target_node)
            
            target.reads = parser.reads
            target.writes = parser.writes
            
class CriticalPass:
   def __init__(self, model):
      self.model = model
      
   def analyze(self):
      globals = self.model.globals
      nonlocals = self.model.nonlocals
      
      for target in self.model.thread_targets:
         print(target)
         shared_reads = target.reads
         shared_writes = target.writes

         if shared_reads or shared_writes:
            print(f"\nThread routine: {target.name}")

            if shared_reads:
               print("  Reads shared variables:", shared_reads)

            if shared_writes:
               print("  Writes shared variables:", shared_writes)

            print("  WARNING: potential shared state access")
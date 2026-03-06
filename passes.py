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
         
   def visit_Nonlocal(self, node):
      for name in node.names:
         self.model.nonlocals[name] = node
         
class ThreadPass(PCNodeVisitor):
   def __init__(self, model):
      self.model = model
      
   def visit_Call(self, node):
      if isinstance(node.func, ast.Name) and node.func.id == "Thread":
         for kw in node.keywords:
            if kw.arg == "target":
               name = self.get_target(kw.value)
               if name:
                  target = ThreadTarget(name, node)
                  self.model.thread_targets.append(target)
                  
   def get_target(self, node):
      if isinstance(node, (ast.Name, ast.Attribute)):
         match node:
            case ast.Name:
               return node.id
            case ast.Attribute:
               return node.attr
      return None
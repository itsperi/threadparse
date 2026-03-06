import ast
import sys
import gitutils
from collections import defaultdict
'''
The point of this project is to 
1. Utilize the ast library to parse Python program files
2. Use the ASTs generated to traverse the logical structure
3. Find code flows that may involve shared resources among spawned threads
'''

# Custom NodeVisitor that enables parent tracking for upwards recursion
class PCNodeVisitor(ast.NodeVisitor):
    def generic_visit(self, node):
        for child in ast.iter_child_nodes(node):
            child.parent = node
            self.visit(child)
            
# Subclass of ast.NodeVisitor to find specific function/method calls
class TParser(PCNodeVisitor):
   def __init__(self):
      self.calls = defaultdict(list)
      self.defs = defaultdict(list)
      self.globals = defaultdict(list)
      self.nonlocals = defaultdict(list)
   
   def visit_Call(self, node):
      # Calls can either be function or method calls
      # functions in the ast have type ast.Name
      # methods in the ast have ast.Attribute
      if isinstance(node.func, ast.Name):
         if node.func.id == "Thread":
            for kw in node.keywords:
               if kw.arg == "target":
                  self.calls[node.func.id].append(node)
            
      elif isinstance(node.func, ast.Attribute):
         if node.func.attr == "Thread":
            for kw in node.keywords:
               if kw.arg == "target":
                  self.calls[node.func.attr].append(node)
            
      # Traverse down the rest of the tree
      # Since this function is a custom visit method,
      # we need to explicitly call original generic_visit
      self.generic_visit(node)
      
   def visit_Global(self, node):
      for name in node.names:
         self.globals[name].append(node)
         print(f"Global {name}: {node.lineno}")
         
   def visit_Nonlocal(self, node):
      for name in node.names:
         self.nonlocals[name].append(node)
         print(f"Nonlocal {name}: {node.lineno}")
      
   # Run this pass before any others to collect
   # rudimentary data about thread call sites/targets
   def first_pass(self, node):
      # You should only pass the root node here
      node.parent = None
      # MUST NOT BE SUPER()
      self.visit(node)
      
      
def print_locations(tree):
   if tree is None: 
      return
   
   scanner = TParser()
   scanner.first_pass(tree)
   
   for func, nodes in scanner.calls.items():
      for node in nodes:
         print(f"{func} call: {node.lineno}")
         
   for funcdef, nodes in scanner.defs.items():
      for node in nodes:
         print(f"{funcdef} def: {node.lineno}")
            
def main():
   args = sys.argv
   if len(args) > 1:
      for file in range(1, len(args)):
         tree = gitutils.build_ast_from_file(args[file])
         print_locations(tree)

if __name__ == "__main__":
   main()
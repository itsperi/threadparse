import ast
import sys
import gitutils

'''
The point of this project is to 
1. Utilize the ast library to parse Python program files
2. Use the ASTs generated to traverse the logical structure
3. Find code flows that may involve shared resources among spawned threads
'''

# Subclass of ast.NodeVisitor to find specific function/method calls
class function_call_finder(ast.NodeVisitor):
   def __init__(self, target_name):
      self.target_name = target_name
      self.calls = {}
      self.global_names = set()
      self.nonlocal_names = set()
      self.globals = []
      self.nonlocals = []
   
   def visit_Call(self, node):
      # Calls can either be function or method calls
      # functions in the ast have type ast.Name
      # methods in the ast have ast.Attribute
      if isinstance(node.func, ast.Name):
         if node.func.id == self.target_name:
            self.calls[self.target_name] = node
            
      elif isinstance(node.func, ast.Attribute):
         if node.func.attr == self.target_name:
            self.calls[self.target_name] = node
            
      # Traverse down the rest of the tree
      # Since this function is a custom visit method,
      # we need to explicitly call generic_visit
      self.generic_visit(node)
      
   def visit_Assign(self, node):
      pass
      
   def visit_Global(self, node):
      for name in node.names:
         self.global_names.add(name)
      self.globals.append((node.lineno, node.col_offset))
      
   def visit_Nonlocal(self, node):
      for name in node.names:
         self.nonlocal_names.add(name)
      self.nonlocals.append((node.lineno, node.col_offset))
      
def print_locations(tree, target):
   if tree is None: 
      return
   
   threadcutter = function_call_finder(target)
   threadcutter.visit(tree)
   
   for func, node in threadcutter.calls.items():
      print(f"Function:{func} at {node.lineno}, {node.col_offset}")
   print(f"Set of globals: {threadcutter.global_names}")
   print(f"Set of locals: {threadcutter.nonlocal_names}")
   print(f"Visited globals: {threadcutter.globals}")
   print(f"Visited nonlocals: {threadcutter.nonlocals}")

         
def main():
   args = sys.argv
   if len(args) > 1:
      for file in range(1, len(args)):
         tree = gitutils.build_ast_from_file(args[file])
         print_locations(tree, "start")

if __name__ == "__main__":
   main()
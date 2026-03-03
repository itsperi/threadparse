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
      self.func_calls = []
      self.method_calls = []
   
   def visit_Call(self, node):
      if isinstance(node.func, ast.Name):
         if node.func.id == self.target_name:
            self.func_calls.append((node.lineno, node.col_offset))
            
      elif isinstance(node.func, ast.Attribute):
         if node.func.attr == self.target_name:
            self.method_calls.append((node.lineno, node.col_offset))
            
      # Traverse down the rest of the tree
      self.generic_visit(node)
      
def print_locations(tree, target):
   if tree is None: 
      return
   
   threadcutter = function_call_finder(target)
   threadcutter.visit(tree)
   
   print(f"Visited function calls for {target}: {threadcutter.func_calls}")
   print(f"Visited method calls for {target}: {threadcutter.method_calls}")

         
def main():
   args = sys.argv
   if len(args) > 1:
      for file in range(1, len(args)):
         tree = gitutils.build_ast_from_file(args[file])
         print_locations(tree, "start")

if __name__ == "__main__":
   main()
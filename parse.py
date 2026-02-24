import ast
import sys

'''
The point of this project is to 
1. Utilize the ast library to parse Python program files
2. Use the ast's generated to traverse the logical structure
3. Find code flows that may involve shared resources among spawned threads
'''

def build_ast_from_file(filepath: str):
   try:
      with open(filepath) as f:
         program = f.read()
   except FileNotFoundError:
      print(f"The given file <{filepath}> doesn't exist")
      return None
   
   try:
      tree = ast.parse(program)
   except Exception as e:
      print(f"There was an error parsing the program into an AST: {e}")
      return None
   
   return tree

def print_tree(tree):
   print(ast.dump(tree, indent=4))

def main():
   args = sys.argv
   if len(args) > 1:
      for file in range(1, len(args)):
         tree = build_ast_from_file(args[file])
         print_tree(tree)

if __name__ == "__main__":
   main()
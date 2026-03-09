import ast
import sys
import gitutils
from passes import SymbolPass, ThreadPass, TargetUpdate, CriticalPass
from model import ProgramModel
'''
The point of this project is to 
1. Utilize the ast library to parse Python program files
2. Use the ASTs generated to traverse the logical structure
3. Find code flows that may involve shared resources among spawned threads
'''

class Analyzer:
   def __init__(self, tree):
      self.tree = tree
      self.model = ProgramModel()
      
   def run(self):
      SymbolPass(self.model).visit(self.tree)
      ThreadPass(self.model).visit(self.tree)
      TargetUpdate(self.model).update_thread_accesses()
      CriticalPass(self.model).analyze_shared_vars()
            
def main():
   args = sys.argv
   if len(args) > 1:
      for file in range(1, len(args)):
         tree: ast.Module = gitutils.build_ast_from_file(args[file])
         threadcutter = Analyzer(tree)
         threadcutter.run()

if __name__ == "__main__":
   main()
import sys
import gitutils as util
from passes import SymbolPass, ScopePass, ThreadPass, TargetUpdate, CriticalPass
from model import ProgramModel

'''
The point of this project is to 
1. Utilize the ast library to parse Python program files
2. Use the ASTs generated to traverse the logical structure
3. Find code flows that may involve shared resources among spawned threads
'''


'''
An instance of an Analyzer makes multiple
AST passes on a program and stores relevant
info within a program model that is updated
for each pass done. Any relevant details are
printed within each stage.
'''
class Analyzer:
   def __init__(self, tree):
      self.tree = tree
      self.model = ProgramModel()
      
   def run(self):
      SymbolPass(self.model).visit(self.tree)
      ScopePass(self.model).visit(self.tree)
      ThreadPass(self.model).visit(self.tree)
      TargetUpdate(self.model).update_thread_accesses()
      CriticalPass(self.model).analyze_shared_vars()
            
'''
Takes a list of filepaths (namely ones that lead
to files/*.py) and runs an analysis on each
'''
def parse_files(paths: list[str]):
   for path in paths:
         print(f"Parsing {path}...")
         tree = util.build_ast_from_filepath(path)
         if not tree:
            continue
         threadcutter = Analyzer(tree)
         threadcutter.run()
         print("---------------------")

'''
Takes the repo urls from repos.txt, 
grabs contents using HTTP requests,
and runs an analysis on each .py file in the repo
'''
# def parse_repos():
#    with open("repos.txt", "r") as repos:
#       for url in repos.readlines():
#          repo = util.GitHubPyGrab(url)
#          files: dict[str, str] = repo.fetch_all()
#          print(f"{len(files)} file(s) found in {url}")
#          for path, program in files.items():
#             print(f"Parsing {path}...")
#             tree = util.build_ast_from_program(path, program)
#             if not tree:
#                continue
#             threadcutter = Analyzer(tree)
#             threadcutter.run()
#             print("---------------------")
            
'''
Command line arguments tell how to run analyses
whether thru local downloads, repo pulling, or 
manual file naming within the project directory
'''
def main():
   args = sys.argv
   if len(args) > 1:
      if "--files" in args or "-f" in args:
         print("Reading files from files/...")
         files = util.get_filepaths_in_dir("files")
         parse_files(files)
            
      elif "--repos" in args or "-r" in args:
         print("Reading files from repos in repos.txt...")
         parse_repos()
                  
      else:
         files = args[1:]
         parse_files(files)

if __name__ == "__main__":
   main()
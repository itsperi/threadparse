import ast
import sys
import gitutils
from passes import SymbolPass, ThreadPass, TargetUpdate, CriticalPass
from model import ProgramModel
import argparse
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
      
def parse_files(paths: list[str]):
   for path in paths:
         print(f"Parsing {path}...")
         tree = gitutils.build_ast_from_filepath(path)
         if not tree:
            continue
         threadcutter = Analyzer(tree)
         threadcutter.run()
         print("---------------------")

def parse_repos():
   with open(repos.txt) as repos:
      for url in repos.readlines():
         repo = gitutils.GitHubPyGrab(url)
         files: dict[str, str] = repo.fetch_all()
         print(f"{len(files)} found in {url}")
         for path, program in files.items():
            print(f"Parsing {path}...")
            tree = gitutils.build_ast_from_program(path, program)
            if not tree:
               continue
            threadcutter = Analyzer(tree)
            threadcutter.run()
            print("---------------------")
         
            
def main():
   args = sys.argv
   if len(args) > 1:
      if "--files" in args or "-f" in args:
         print("Reading files from files/...")
         files = gitutils.get_filepaths_in_dir("files")
         parse_files(files)
            
      elif "--repos" in args or "-r" in args:
         print("Reading repos from repos.txt...")
         with open("repos.txt", mode="r") as repos:
            for line in repos.readlines():
               owner, repo = gitutils.parse_github_url(line)
               files = gitutils.fetch_files(owner, repo)
               parse_files(files)
                  
      else:
         files = args[1:]
         parse_files(files)

if __name__ == "__main__":
   main()
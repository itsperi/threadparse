import ast
import sys
import requests

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
      print(f"There was an error parsing <{filepath}> into an AST: {e}")
      return None
   
   return tree

def print_thread_locations(tree):
   if tree is None: 
      return
   
   threadcutter = function_call_finder("start")
   threadcutter.visit(tree)
   
   print(f"Visited function calls for {threadcutter.target_name}: {threadcutter.func_calls}")
   print(f"Visited method calls for {threadcutter.target_name}: {threadcutter.method_calls}")

# get_paths_from_repo
# Returns a list of filepaths in a given repo spec for exclusively Python files
# Input: owner - author of target github repo
#        repo - name of the target github repo (must be public)
#        branch - name of the branch of target repo (defaults to main, may be master in some cases)
# Output: List of filepaths to files with .py extension in the entire repo, may be empty if none exist
def get_paths_from_repo(owner, repo, branch="main"):
   url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
   r = requests.get(url)
   r.raise_for_status()
   tree = r.json()["tree"]
   return [item["path"] for item in tree if item["path"].endswith(".py")]   

# get_pytxt_from_path
# Returns a list of python file contents given repo and filepath spec
# Input: same as above, plus path
#        path - filepath in the repo to .py file
def get_pytxt_from_path(owner, repo, path, branch="main"):
   raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
   r = requests.get(raw_url)
   if r.status_code == 200:
      return r.text
   return None

# fetch_files
# Returns a list of Python file contents from a given repo spec
# Input: owner - author of target github repo
#        repo - name of the target github repo (must be public)
#        branch - name of the branch of target repo (defaults to main, may be master in some cases)
def fetch_files(owner, repo, branch="main"):
   paths = get_paths_from_repo(owner, repo, branch)
   if paths:
      contents = []
      for path in paths:
         contents.append(get_pytxt_from_path(owner, repo, path, branch))
      return contents
   return []
         
def main():
   args = sys.argv
   if len(args) > 1:
      for file in range(1, len(args)):
         tree = build_ast_from_file(args[file])
         print_thread_locations(tree)

if __name__ == "__main__":
   main()
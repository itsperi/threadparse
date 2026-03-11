import ast
import requests
from pathlib import Path   
from urllib.parse import urlparse

def build_ast_from_filepath(filepath: str):
   try:
      with open(filepath) as f:
         program = f.read()
   except FileNotFoundError:
      print(f"The given file <{filepath}> doesn't exist")
      return None
   
   try:
      if "import threading" not in program:
         print(f"Threading module not found in {filepath}")
         tree = None 
      else:
         tree = ast.parse(program)
   except Exception as e:
      print(f"There was an error parsing <{filepath}> into an AST: {e}")
      return None
   
   return tree

def build_ast_from_program(filepath: str, file: str):
   try:
      if "import threading" not in file:
         print(f"Threading module not found in {filepath}")
         tree = None
      else:
         tree = ast.parse(file)
   except Exception as e:
      print(f"There was an error parsing <{filepath}> into an AST: {e}")
   
   return tree

def get_default_branch(owner, repo):
   url = f"https://api.github.com/repos/{owner}/{repo}"
   r = requests.get(url)
   r.raise_for_status()
   return r.json()["default_branch"]

# get_paths_from_repo
# Returns a list of filepaths in a given repo spec for exclusively Python files
# Input: owner - author of target github repo
#        repo - name of the target github repo (must be public)
#        branch - name of the branch of target repo (defaults to main, may be master in some cases)
# Output: List of filepaths to files with .py extension in the entire repo, may be empty if none exist
def get_paths_from_repo(owner, repo, branch=None):
   if branch is None:
      branch = get_default_branch(owner, repo)

   url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"

   r = requests.get(url)
   r.raise_for_status()

   tree = r.json()["tree"]

   return [
      item["path"]
      for item in tree
      if item["type"] == "blob" and item["path"].endswith(".py")
   ]  

# get_pytxt_from_path
# Returns a list of python file contents given repo and filepath spec
# Input: same as above, plus path
#        path - filepath in the repo to .py file
# Output: The text of a python file at the given path
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
def fetch_files(owner, repo, branch=None):
   paths = get_paths_from_repo(owner, repo, branch)

   contents = []

   for path in paths:
      txt = get_pytxt_from_path(owner, repo, path, branch)
      if txt is not None:
         contents.append(txt)

   return contents

# get_pytext_in_dir
# Returns a list of Python file contents from a local directory
# Input: dir - a directory in the root project folder, or absolute path to one
# Output: filepaths - a list of paths to python files for parsing
def get_filepaths_in_dir(dir):
   return [str(p) for p in Path(dir).rglob("*.py")]
      

# parse_github_url
# Returns two strings from a github url denoting its owner and repo
# Input: url - string rep of the url
# Output: owner, repo - parsed components
def parse_github_url(url):
   if "github.com" not in url:
      return (None, None)

   parts = url.strip().split("github.com/")[1].split("/")
   owner = parts[0].strip()
   repo = parts[1].replace(".git","").strip()

   return owner, repo

class GitHubPyGrab:
   def __init__(self, repo_url):
      self.repo_url = repo_url.rstrip("/")
      self.owner, self.repo = self._parse_repo_url()
      self.api_base = f"https://api.github.com/repos/{self.owner}/{self.repo}/contents"

   def _parse_repo_url(self):
      """
      Extract owner and repo name from a GitHub URL
      """
      path = urlparse(self.repo_url).path.strip("/")
      parts = path.split("/")
      if len(parts) < 2:
         raise ValueError("Invalid GitHub repo URL")
      return parts[0], parts[1]

   def _get_contents(self, path=""):
      url = f"{self.api_base}/{path}"
      r = requests.get(url)
      r.raise_for_status()
      return r.json()

   def _collect_py_files(self, path=""):
      files = []
      contents = self._get_contents(path)

      for item in contents:
         if item["type"] == "file" and item["name"].endswith(".py"):
               files.append(item["download_url"])

         elif item["type"] == "dir":
               files.extend(self._collect_py_files(item["path"]))

      return files

   def fetch_all(self):
      """
      Returns dictionary: {filepath: file contents}
      """
      py_files = self._collect_py_files()
      result = {}

      for url in py_files:
         r = requests.get(url)
         r.raise_for_status()
         path = url.split(f"{self.repo}/")[-1]
         result[path] = r.text

      return result
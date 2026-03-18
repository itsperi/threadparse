import ast
import requests
from pathlib import Path   
from urllib.parse import urlparse
import re

pattern = re.compile(
   r'^\s*(?:import\s+.*\bthreading\b|from\s+threading\s+import\b)',
   re.MULTILINE
)

def uses_threading(code: str) -> bool:
   return bool(pattern.search(code))

'''
Opens the file given as the filepath
and returns the resultant AST only 
if it contains the threading library
'''
def build_ast_from_filepath(filepath: str):
   try:
      with open(filepath) as f:
         program = f.read()
   except FileNotFoundError:
      print(f"The given file <{filepath}> doesn't exist\n")
      return None
   
   try:
      if uses_threading(program):
         tree = ast.parse(program)
      else:
         print(f"No threading usage detected\n")
         return None
   except Exception as e:
      print(f"There was an error parsing <{filepath}> into an AST: {e}\n")
      return None
   
   return tree

'''
Parses the given file string
and returns the resultant AST only 
if it contains the threading library
'''
def build_ast_from_program(filepath: str, file: str):
   try:
      if uses_threading(file):
         tree = ast.parse(file)
      else:
         print(f"No threading usage detected\n")
         return None
   except Exception as e:
      print(f"There was an error parsing <{filepath}> into an AST: {e}\n")
      return None
   
   return tree

'''
Returns a list of Python file contents from a local directory
Input: dir - a directory in the root project folder, or absolute path to one
Output: filepaths - a list of paths to python files for parsing
'''
def get_filepaths_in_dir(dir):
   return [str(p) for p in Path(dir).rglob("*.py")]


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

   """
   Returns dictionary: {filepath: file contents}
   """
   def fetch_all(self):
      py_files = self._collect_py_files()
      result = {}

      for url in py_files:
         r = requests.get(url)
         r.raise_for_status()
         path = url.split(f"{self.repo}/")[-1]
         result[path] = r.text

      return result
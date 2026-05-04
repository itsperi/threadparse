import sys, json
import gitutils as util
from passes import (
   ImportDetection,
   SymbolPass,
   TypeInferencePass,
   ScopePass,
   ThreadPass,
   CallGraphPass,
   ThreadExpansion,
   TargetUpdate,
   ClassResolutionPass,
   SharedUpdate,
   CriticalPass
)
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
   def __init__(self, tree, name="", mode: str | None = None):
      self.tree = tree
      self.model = ProgramModel(name=name)
      self.mode = mode

   def run(self):
      if not self.tree:
         if self.mode == "verbose":
            print(f"No AST to analyze: {self.model.name}")
         return None
      import_detection = ImportDetection()
      import_detection.visit(self.tree)
      if not import_detection.uses_threading:
         return None
      SymbolPass(self.model).visit(self.tree)
      TypeInferencePass(self.model).visit(self.tree)
      ScopePass(self.model).visit(self.tree)
      ThreadPass(self.model).visit(self.tree)
      CallGraphPass(self.model).visit(self.tree)
      ThreadExpansion(self.model).expand()
      TargetUpdate(self.model).update_thread_accesses()
      ClassResolutionPass(self.model).resolve_classes()
      SharedUpdate(self.model).update_shared_vars()
      result = CriticalPass(self.model, 
                            mode=self.mode
                            ).analyze_shared_vars()
      
      # ================= DEBUGGING =================
      # for func, node in self.model.functions.items():
      #    print(f"Function: {func}")
      #    print(f"  Globals: {self.model.function_globals.get(func, set())}")
      #    print(f"  Nonlocals: {self.model.function_nonlocals.get(func, set())}")
      #    print(f"  Calls: {self.model.call_graph.get(func, set())}")
      
      # for target in self.model.thread_targets:
      #    print(f"Thread Target: {target.name} (in class {target.class_name})")
      #    print(f"  Reads: {list(target.reads.keys())}")
      #    print(f"  Writes: {list(target.writes.keys())}")
      #    print(f"  Calls: {list(target.calls.keys())}")
      #    for var, accesses in target.sibling_writes.items():
      #       print(f"  Sibling Writes to {var}:")
      #       for sibling, nodes in accesses.items():
      #          print(f"    {sibling}: {len(nodes)} writes")
      #    for var, accesses in target.sibling_reads.items():
      #       print(f"  Sibling Reads to {var}:")
      #       for sibling, nodes in accesses.items():
      #          print(f"    {sibling}: {len(nodes)} reads")
         
      # for cl, node in self.model.class_attrs.items():
      #    print(f"Class Attribute: {cl}")
         
      # for var, nodes in self.model.shared_vars.items():
      #    print(f"Shared Variable: {var} accessed in:")
      #    for target, accesses in nodes.items():
      #       print(f"  {target}: {len(accesses)} accesses")

      # Wrapper to identify and collect data later
      return {
         "name": self.model.name,
         "unsafe": result is not None and result.get("unsafe", False),
         "violations": result.get("violations", set()) if isinstance(result, dict) else set(),
         "detail": result,
      }
            
'''
Takes a list of filepaths (namely ones that lead
to files/*.py) and runs an analysis on each
'''
def parse_files(paths: list[str], mode: str | None = None, json_out: str | None = None):
   PRECEDENCE = ["SHARED LIST", "SHARED DICT", "SHARED SET", "SC"]
   unsafe_files = []
   all_results  = []
   json_data = {}

   for path in paths:
      tree   = util.build_ast_from_filepath(path, mode=mode)
      result = Analyzer(tree, 
                        name=path, 
                        mode=mode, 
                        ).run()
      if result is None:
         continue

      all_results.append(result.get("detail", {}))

      if result.get("unsafe", False):
         unsafe_files.append((path, result.get("violations", set())))
         
      json_data[path] = result 
      
         
   if json_out:
      with open(json_out, "w") as f:
         json.dump(json_data, f, indent=2)
      if mode != "silent":
         print(f"\nResults written to {json_out}")

   if mode != "silent":
      print("\n====================")
      if unsafe_files:
         print(f"{len(unsafe_files)} unsafe file(s) detected:")
         for path, violations in unsafe_files:
            kinds = [k for k in PRECEDENCE if k in violations]
            print(f"  - {path} [{', '.join(kinds)}]")
      else:
         print("No unsafe threading detected.")

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
   args = sys.argv[1:]
   mode = None
   json_out = None
         
   if len(args) > 1:
      if "--verbose" in args or "-v" in args:
         mode = "verbose"
         args = [a for a in args if a not in ("--verbose", "-v")]
      
      if "-o" in args or "--output" in args:
         o_index = args.index("-o") if "-o" in args else args.index("--output")
         if o_index < len(args) - 1:
            json_out = args[o_index + 1]
            args = args[:o_index] + args[o_index + 2:]
         else:
            print("Error: -o flag provided without filename")
            sys.exit(1)
            
      if "-s" in args or "--silent" in args:
         mode = "silent"
         args = [a for a in args if a not in ("-s", "--silent")]
            
      paths = util.get_all_filepaths(args)
      
      if not paths:
         print("No valid file paths provided.")
         sys.exit(1)
         
      parse_files(paths, mode=mode, json_out=json_out)
      
   elif len(args) != 1 and ("--help" in args or "-h" in args):
      print("Usage: python parse.py [-v | --verbose] [-s | --silent] [[-o | --output] <filename>] <file_or_dir_paths>")
      print("  -v, --verbose       Enable verbose output")
      print("  -s, --silent        Enable silent output")
      print("  -o <filename>, --output <filename>   Output results to JSON file")
      
   else:
      print("Invalid command line arguments, use --help for usage info")

if __name__ == "__main__":
   main()
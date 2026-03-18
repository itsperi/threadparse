import ast
from dataclasses import dataclass, field

@dataclass
class ThreadTarget:
   name: str
   node: ast.AST
   reads: dict[str, ast.AST] = field(default_factory=dict)
   writes: dict[str, ast.AST] = field(default_factory=dict)
      
class Scope:
   def __init__(self, parent=None):
      self.parent = parent
      self.locals = set()
      self.globals = set()
      self.nonlocals = set()
   
class ProgramModel:
   def __init__(self):
      self.functions : dict[str, ast.AST] = {}
      self.globals : dict[str, ast.AST] = {}
      self.nonlocals : dict[str, ast.AST] = {}
      self.thread_targets : list[ThreadTarget]= []
      self.seen_targets : set = set()
      self.function_scopes : dict[str, Scope] = {}
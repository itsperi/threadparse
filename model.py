import ast
from dataclasses import dataclass, field
from collections import defaultdict

@dataclass
class ThreadTarget:
   name: str
   node: ast.AST
   reads = field(default_factory=set)
   writes = field(default_factory=set)
   
class ProgramModel:
   def __init__(self):
      self.functions = defaultdict[list]
      self.globals = defaultdict[list]
      self.nonlocals = defaultdict[list]
      self.thread_targets = []
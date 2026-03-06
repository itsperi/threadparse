import ast
from dataclasses import dataclass, field
from collections import defaultdict

@dataclass
class ThreadTarget:
   name: str
   node: ast.AST
   reads: set = field(default_factory=dict)
   writes: set = field(default_factory=dict)
   
class ProgramModel:
   def __init__(self):
      self.functions = {}
      self.globals = {}
      self.nonlocals = {}
      self.thread_targets = []
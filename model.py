import ast
from dataclasses import dataclass, field

@dataclass
class ThreadTarget:
   name: str
   class_name: str
   node: ast.AST
   reads: dict[str, list[ast.AST]] = field(default_factory=dict)
   writes: dict[str, list[ast.AST]] = field(default_factory=dict)
   calls: dict[str, list[ast.AST]] = field(default_factory=dict)
   # If the target belongs to a class, we need to analyze instance state
   sibling_writes: dict[str, dict[str, list]] = field(default_factory=dict)
   sibling_reads: dict[str, dict[str, list]] = field(default_factory=dict)
   parent_target: str = None
   root_target: str = None
      
class Scope:
   def __init__(self, parent=None):
      self.parent = parent
      self.locals = set()
      self.globals = set()
      self.nonlocals = set()
   
class ProgramModel:
   def __init__(self, name=""):
      # Data about the program as a whole
      self.name: str = name
      self.total_lines: int = 0
      self.globals : dict[str, ast.AST] = {}
      self.nonlocals : dict[str, ast.AST] = {}
      self.module_vars : dict[str, ast.AST] = {}
      self.var_types : dict[str, str] = {}
      
      # Data about functions and relative scopes
      self.functions : dict[str, ast.AST] = {}
      self.function_scopes : dict[str, Scope] = {}
      self.function_globals: dict[str, set[str]] = {}
      self.function_nonlocals: dict[str, set[str]] = {}
      self.call_graph : dict[str, set[str]] = {}

      # Data about classes and their methods
      self.method_to_class : dict[str, str] = {}
      self.class_methods: dict[str, set[str]] = {}
      self.class_attrs: dict[str, ast.AST] = {}
      self.thread_subclasses: set[str] = set()
      
      # Data about threads and their accesses
      self.executors: dict[str, set[ast.AST]] = {}
      self.thread_targets : list[ThreadTarget] = []
      self.thread_vars : set[str] = set()
      self.seen_targets : set[str] = set()
      self.thread_start_lines : list[int] = []
      self.shared_vars : dict[str, dict[str, list[ast.AST]]] = {}
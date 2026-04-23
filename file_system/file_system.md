# Low-Level Design: In-Memory Linux File System — Uber SDE-2 Interview Guide

> **Problem (2025 Uber SDE-2 variant):** Implement core Linux file system commands — `mkdir`, `cd`, `ls`, `pwd`. Use an N-ary tree for the directory hierarchy. Error handling expected.
>
> **Problem (2022 variant):** Implement `create`, `delete`, `move` on an in-memory file system.
>
> This guide covers **both** framings in one codebase so you're ready either way. Round is ~45 mins: 10-12 mins architecture + 15-20 mins code + ~10 mins follow-ups.

---

## Step 1: Understanding & Clarifying Requirements (2-3 mins)

> **What to say:** "Before I jump in, let me restate the problem and clarify a few things so we're aligned on scope."

### Your Understanding (State This)

- Build an in-memory file system that mimics Linux semantics
- Supports directories and files organized as an N-ary tree, rooted at `/`
- Supports core shell commands: `mkdir`, `cd`, `ls`, `pwd` (and extensibly `touch`, `rm`, `mv`)
- Supports both absolute paths (`/a/b/c`) and relative paths (`./x`, `../y`, `x/y`)
- Every operation must validate and surface clear errors (not found, already exists, not a directory, etc.)

### Clarifying Questions (Ask These)

| # | Question | Expected Answer |
|---|----------|-----------------|
| 1 | Is this in-memory only, or should we persist to disk? | In-memory is fine |
| 2 | Single-user single-session, or should we worry about concurrent shells? | Single-session for now, but design so multi-session is not a rewrite |
| 3 | Do files have contents, or just names? | Just names + optional content; keep it simple |
| 4 | Do we need permissions (rwx, owners)? | No, but mention how they'd slot in |
| 5 | Should `mkdir` be recursive (like `mkdir -p`), or fail if parent missing? | Non-recursive by default, mention the flag |
| 6 | Should `ls` support flags like `-l`, `-R`, `-a`? | Basic `ls` (names only); `-R` as extension |
| 7 | Case-sensitive names? | Yes, Linux-style |
| 8 | What should `rm` do on a non-empty directory? | Error by default; `-r` as extension |

### Confirmed Requirements

1. **Tree-structured** file system rooted at `/`, N-ary (dir can have many children)
2. **Core commands:** `mkdir <path>`, `cd <path>`, `ls [path]`, `pwd`
3. **Also (2022 variant):** `create <path>`, `delete <path>`, `move <src> <dst>`
4. **Path support:** absolute (`/a/b`), relative (`a/b`, `./a`, `../a`), special tokens `.` and `..`
5. **Error handling:** not-found, already-exists, not-a-directory, invalid-path, non-empty-delete
6. **Extensible:** easy to add permissions, file contents, symlinks, persistence

---

## Step 2: Core Entities & Relationships (3-4 mins)

> **What to say:** "Let me identify the entities. The two big insights are: (1) files and directories should share a base type so operations compose recursively, and (2) each shell command is really its own little unit of work — that maps very cleanly to the Command pattern."

### Entities

| Entity | Responsibility |
|--------|---------------|
| **Node (ABC)** | Base type for anything in the tree — has name, parent, created_at |
| **File** | Leaf node; holds (optional) content |
| **Directory** | Internal node; holds `children: dict[str, Node]` (the N-ary tree) |
| **Path** | Parses a path string into components, tracks absolute vs relative |
| **PathResolver** | Walks the tree from a starting node following path components (handles `.`, `..`) |
| **FileSystem** | Holds the root node; exposes primitive ops (`mkdir`, `touch`, `rm`, `mv`, `ls`, `get_node`) |
| **Session (Shell)** | Per-user state — tracks `cwd`; dispatches commands |
| **Command (ABC)** | Encapsulates one shell command; has `execute(session, args)` |
| **MkdirCommand / CdCommand / LsCommand / PwdCommand / TouchCommand / RmCommand / MvCommand** | Concrete commands |
| **CommandRegistry** | Maps command name (`"mkdir"`) → Command instance; dispatches input lines |
| **FSError (+ subclasses)** | Typed errors: `NotFoundError`, `AlreadyExistsError`, `NotADirectoryError`, `NotEmptyError` |

### Relationships (Class Diagram)

```
Node (ABC: name, parent, created_at)
  |-- File       (leaf, content: str)
  |-- Directory  (children: dict[str, Node])   <-- the N-ary tree lives here

FileSystem
  |--- has-a ---> Directory (root "/")
  |--- uses -----> PathResolver

Session (Shell)
  |--- has-a ---> FileSystem
  |--- has-a ---> Directory (cwd)           # current working directory
  |--- has-a ---> CommandRegistry

CommandRegistry
  |--- has-many -> Command

Command (ABC)
  |-- MkdirCommand
  |-- CdCommand
  |-- LsCommand
  |-- PwdCommand
  |-- TouchCommand   (create)
  |-- RmCommand      (delete)
  |-- MvCommand      (move)
```

### N-ary Tree Shape

```
                  / (Directory, root)
                 / \
              usr   home (Directory)
             /       \
           bin        aman (Directory)
          /          /      \
        ls         docs    readme.txt (File)
                   /
              notes.txt (File)
```

Every `Directory` has a `children` dict keyed by name — O(1) lookup per path component.

---

## Step 3: Design Patterns & Tradeoffs (2-3 mins)

> **What to say:** "I'd lean on four patterns. Two of them are load-bearing — Composite and Command — and two are lighter-weight conveniences."

### Pattern 1: Composite Pattern (File + Directory as Node) — THE STAR

**Why:** Files and directories live in the same tree and callers shouldn't care which type they've got when walking or moving them. Without a shared base, every operation branches on `isinstance`.

**How:** `Node` ABC with `name`, `parent`. `File` and `Directory` both extend it. `Directory.children` holds `Node` — not specifically File or Directory — so a directory can contain a mix.

**Tradeoff:** A tiny amount of type-checking still leaks in (e.g., `cd` must fail on a file, `ls` must fail on a file). But moving, renaming, path resolution, tree printing — all uniform. Worth it.

```
Node (ABC)
  |-- File       (leaf)
  |-- Directory  (has children: dict[str, Node])
```

### Pattern 2: Command Pattern (each shell command as a class) — THE SECOND PILLAR

**Why:** The problem literally says "implement `mkdir`, `cd`, `ls`, `pwd`." That's four verbs. A giant `if command == "mkdir": ... elif command == "cd": ...` block in the shell is the obvious smell. Command pattern isolates each verb.

**How:** `Command` ABC with `execute(session, args)`. One class per command. A `CommandRegistry` dict dispatches by name.

**Tradeoff:** More classes — but each command is ~5-15 lines, easy to test, and adding `rm`/`mv`/`cp` is literally "write one class, register it." This is the Open/Closed Principle demo the interviewer wants to see.

```
Command (ABC)
  |-- MkdirCommand, CdCommand, LsCommand, PwdCommand
  |-- TouchCommand, RmCommand, MvCommand        (2022 variant)
  |-- (future: CpCommand, FindCommand, CatCommand, ...)
```

### Pattern 3: Singleton (FileSystem) — light

**Why:** One filesystem per process. Global access simplifies wiring.

**How:** Module-level instance, or a `FileSystem.get_instance()` if they push on it.

**Tradeoff:** Testability suffers if abused — so in real code we'd inject it. For a 45-min interview, module-level is fine; mention the tradeoff.

### Pattern 4: Factory (Node Creation) — optional

**Why:** If we later add permissions, metadata, inode numbers, symlinks — centralized creation keeps invariants in one place.

**How:** `NodeFactory.create_file(name, parent)` / `create_directory(name, parent)`.

**Tradeoff:** Overkill for the base problem; mention it as an extensibility point.

### (Optional) Pattern 5: Strategy (PathResolver)

**Why:** Absolute paths start from root, relative paths start from `cwd`, `~` paths start from home. One dispatch rule, different starting points — classic Strategy.

**How:** In practice I'll inline this in a single `resolve_path()` method. Worth *naming* the pattern in the interview if they push on path semantics.

> **Interview tip:** State all four patterns up front, but implement only Composite + Command fully. Mention Factory/Singleton verbally. Don't waste code budget on them.

---

## Step 4: Implementation — Full Reference Design

> This is the complete, pattern-rich version. The tight 15-20 min version is at the bottom.

### 4.1 Errors

```python
class FSError(Exception): pass
class NotFoundError(FSError): pass
class AlreadyExistsError(FSError): pass
class NotADirectoryError(FSError): pass
class NotAFileError(FSError): pass
class NotEmptyError(FSError): pass
class InvalidPathError(FSError): pass
```

### 4.2 Node, File, Directory (Composite)

```python
from abc import ABC, abstractmethod
from datetime import datetime


class Node(ABC):
    def __init__(self, name: str, parent: "Directory | None"):
        self.name = name
        self.parent = parent
        self.created_at = datetime.now()

    @abstractmethod
    def is_directory(self) -> bool: ...


class File(Node):
    def __init__(self, name: str, parent: "Directory", content: str = ""):
        super().__init__(name, parent)
        self.content = content

    def is_directory(self) -> bool:
        return False


class Directory(Node):
    def __init__(self, name: str, parent: "Directory | None"):
        super().__init__(name, parent)
        self.children: dict[str, Node] = {}

    def is_directory(self) -> bool:
        return True

    def add_child(self, node: Node):
        if node.name in self.children:
            raise AlreadyExistsError(f"'{node.name}' already exists in '{self.name}'")
        self.children[node.name] = node
        node.parent = self

    def remove_child(self, name: str) -> Node:
        if name not in self.children:
            raise NotFoundError(f"'{name}' not found in '{self.name}'")
        return self.children.pop(name)

    def get_child(self, name: str) -> Node:
        if name not in self.children:
            raise NotFoundError(f"'{name}' not found in '{self.name}'")
        return self.children[name]
```

### 4.3 Path Resolution

```python
class PathResolver:
    """Resolves a path string to a Node, handling '.', '..', absolute vs relative."""

    @staticmethod
    def split(path: str) -> tuple[bool, list[str]]:
        """Returns (is_absolute, components). Empty components filtered."""
        if not path:
            raise InvalidPathError("Empty path")
        is_absolute = path.startswith("/")
        parts = [p for p in path.split("/") if p]
        return is_absolute, parts

    @staticmethod
    def resolve(path: str, cwd: Directory, root: Directory) -> Node:
        is_absolute, parts = PathResolver.split(path)
        current: Node = root if is_absolute else cwd
        for part in parts:
            if part == ".":
                continue
            if part == "..":
                if current.parent is not None:  # root.parent is None; stays at root
                    current = current.parent
                continue
            if not current.is_directory():
                raise NotADirectoryError(f"'{current.name}' is not a directory")
            current = current.get_child(part)  # raises NotFoundError
        return current

    @staticmethod
    def resolve_parent_and_name(path: str, cwd: Directory, root: Directory) -> tuple[Directory, str]:
        """For create ops: returns (parent_dir, new_name). Parent must exist."""
        is_absolute, parts = PathResolver.split(path)
        if not parts:
            raise InvalidPathError(f"Invalid path: '{path}'")
        name = parts[-1]
        if name in (".", ".."):
            raise InvalidPathError(f"Invalid target name: '{name}'")
        parent_parts = parts[:-1]
        parent_path = ("/" if is_absolute else "") + "/".join(parent_parts)
        if not parent_path:
            parent_path = "." if not is_absolute else "/"
        parent = PathResolver.resolve(parent_path, cwd, root)
        if not parent.is_directory():
            raise NotADirectoryError(f"'{parent.name}' is not a directory")
        return parent, name
```

### 4.4 FileSystem (primitive operations)

```python
class FileSystem:
    def __init__(self):
        self.root = Directory("/", parent=None)

    # ---- primitive operations ----

    def mkdir(self, path: str, cwd: Directory):
        parent, name = PathResolver.resolve_parent_and_name(path, cwd, self.root)
        parent.add_child(Directory(name, parent))  # raises AlreadyExistsError

    def touch(self, path: str, cwd: Directory, content: str = ""):
        parent, name = PathResolver.resolve_parent_and_name(path, cwd, self.root)
        parent.add_child(File(name, parent, content))

    def remove(self, path: str, cwd: Directory, recursive: bool = False):
        node = PathResolver.resolve(path, cwd, self.root)
        if node is self.root:
            raise FSError("Cannot remove root")
        if node.is_directory() and node.children and not recursive:
            raise NotEmptyError(f"'{node.name}' is not empty")
        node.parent.remove_child(node.name)

    def move(self, src_path: str, dst_path: str, cwd: Directory):
        src = PathResolver.resolve(src_path, cwd, self.root)
        if src is self.root:
            raise FSError("Cannot move root")
        # Destination may or may not exist. If it exists AND is a directory, move INTO it.
        # Otherwise treat dst as the new parent+name.
        try:
            dst = PathResolver.resolve(dst_path, cwd, self.root)
            if dst.is_directory():
                new_parent, new_name = dst, src.name
            else:
                raise AlreadyExistsError(f"'{dst_path}' already exists and is not a directory")
        except NotFoundError:
            new_parent, new_name = PathResolver.resolve_parent_and_name(dst_path, cwd, self.root)
        # Prevent moving a directory into its own subtree
        if src.is_directory():
            walker = new_parent
            while walker is not None:
                if walker is src:
                    raise FSError("Cannot move a directory into its own subtree")
                walker = walker.parent
        src.parent.remove_child(src.name)
        src.name = new_name
        new_parent.add_child(src)

    def ls(self, path: str | None, cwd: Directory) -> list[str]:
        node = PathResolver.resolve(path, cwd, self.root) if path else cwd
        if not node.is_directory():
            return [node.name]           # `ls file.txt` is legal in Linux
        return sorted(node.children.keys())

    def absolute_path(self, node: Node) -> str:
        if node is self.root:
            return "/"
        parts = []
        cur = node
        while cur is not None and cur is not self.root:
            parts.append(cur.name)
            cur = cur.parent
        return "/" + "/".join(reversed(parts))
```

### 4.5 Session (Shell state)

```python
class Session:
    def __init__(self, fs: FileSystem):
        self.fs = fs
        self.cwd: Directory = fs.root

    def change_directory(self, path: str):
        node = PathResolver.resolve(path, self.cwd, self.fs.root)
        if not node.is_directory():
            raise NotADirectoryError(f"'{path}' is not a directory")
        self.cwd = node

    def pwd(self) -> str:
        return self.fs.absolute_path(self.cwd)
```

### 4.6 Command Pattern

```python
class Command(ABC):
    @abstractmethod
    def execute(self, session: Session, args: list[str]) -> str | None:
        """Returns output string (or None). Raises FSError on failure."""


class MkdirCommand(Command):
    def execute(self, session, args):
        if len(args) != 1:
            raise InvalidPathError("usage: mkdir <path>")
        session.fs.mkdir(args[0], session.cwd)


class CdCommand(Command):
    def execute(self, session, args):
        if len(args) != 1:
            raise InvalidPathError("usage: cd <path>")
        session.change_directory(args[0])


class PwdCommand(Command):
    def execute(self, session, args):
        return session.pwd()


class LsCommand(Command):
    def execute(self, session, args):
        path = args[0] if args else None
        return "  ".join(session.fs.ls(path, session.cwd))


class TouchCommand(Command):
    def execute(self, session, args):
        if len(args) < 1:
            raise InvalidPathError("usage: touch <path>")
        session.fs.touch(args[0], session.cwd)


class RmCommand(Command):
    def execute(self, session, args):
        recursive = "-r" in args
        paths = [a for a in args if not a.startswith("-")]
        if not paths:
            raise InvalidPathError("usage: rm [-r] <path>")
        for p in paths:
            session.fs.remove(p, session.cwd, recursive=recursive)


class MvCommand(Command):
    def execute(self, session, args):
        if len(args) != 2:
            raise InvalidPathError("usage: mv <src> <dst>")
        session.fs.move(args[0], args[1], session.cwd)
```

### 4.7 CommandRegistry (Dispatcher)

```python
class CommandRegistry:
    def __init__(self):
        self._commands: dict[str, Command] = {}

    def register(self, name: str, command: Command):
        self._commands[name] = command

    def dispatch(self, session: Session, line: str) -> str | None:
        parts = line.strip().split()
        if not parts:
            return None
        name, args = parts[0], parts[1:]
        if name not in self._commands:
            raise FSError(f"command not found: {name}")
        return self._commands[name].execute(session, args)


def build_default_registry() -> CommandRegistry:
    registry = CommandRegistry()
    registry.register("mkdir", MkdirCommand())
    registry.register("cd", CdCommand())
    registry.register("ls", LsCommand())
    registry.register("pwd", PwdCommand())
    registry.register("touch", TouchCommand())
    registry.register("rm", RmCommand())
    registry.register("mv", MvCommand())
    return registry
```

### 4.8 Main Entry / REPL

```python
if __name__ == "__main__":
    fs = FileSystem()
    session = Session(fs)
    registry = build_default_registry()
    while True:
        try:
            line = input(f"{session.pwd()}$ ")
        except (EOFError, KeyboardInterrupt):
            break
        try:
            out = registry.dispatch(session, line)
            if out is not None:
                print(out)
        except FSError as e:
            print(f"error: {e}")
```

---

## Step 5: Extensibility Points (Mention in Interview)

> **What to say:** "The design opens several extension doors without needing changes to existing classes..."

### 5.1 File Contents (read/write)
Add `CatCommand` and `EchoCommand`. `File.content` already exists.

### 5.2 `mkdir -p` (recursive parents)
Extend `MkdirCommand` to take a flag; walk components and create missing ones in a loop.

### 5.3 `ls -l`, `ls -R`
`-l`: return node metadata (size, created_at). `-R`: recurse into child directories — trivial with the Composite shape.

### 5.4 Permissions (rwx)
Add `Permissions` object on `Node` (owner, group, mode). Commands consult it before acting. No structural change to the tree.

### 5.5 Symbolic Links
New `SymLink(Node)` subclass holding a `target_path: str`. `PathResolver` gets a symlink-following step (with loop detection via a visited set).

### 5.6 Persistence
Serialize the tree (DFS) to JSON. `FileSystem.save(path)` / `FileSystem.load(path)`. Nothing else changes.

### 5.7 Concurrency / Multi-Session
Add a lock per `Directory` (or a single global `RWLock` on `FileSystem`). Each `Session` has its own `cwd`, so multi-shell already "works" at the data-model level.

### 5.8 Quotas, Inodes, Timestamps
All live on `Node`. Add fields; no structural change.

### 5.9 Wildcards / Globbing
A `Glob` class expands `*.txt` to a list of resolved nodes. Commands that accept multiple paths accept glob output directly.

---

## Step 6: Interview Flow Summary (Cheat Sheet)

| Time | What To Do | Key Points |
|------|-----------|------------|
| 0-2 min | Restate problem, ask clarifying Qs | In-memory? Persistence? Permissions? mkdir -p? |
| 2-5 min | Entities + N-ary tree + class diagram | Node/File/Directory, FileSystem, Session, Command |
| 5-8 min | Patterns: Composite + Command (deep), mention Singleton/Factory | Composite = unified tree; Command = one class per verb |
| 8-12 min | Walk through the 2 or 3 trickiest primitives on the board | `PathResolver.resolve`, `mv` into own subtree, `rm` non-empty |
| 12-30 min | **Write the code** (see final version below) | Order: errors → Node/File/Directory → PathResolver → FileSystem → Session → Commands → main |
| 30-40 min | Demo: walk through mkdir/cd/ls/pwd/rm/mv interactively in the code | Show error cases too |
| 40-45 min | Extensibility + follow-ups | Permissions, symlinks, persistence, concurrency |

---

## FINAL CODE: Write This in 15-20 Minutes

> Streamlined, runnable, covers both Uber variants (mkdir/cd/ls/pwd AND create/delete/move). ~180 lines. Keeps Composite + Command; inlines path resolution.

```python
from abc import ABC, abstractmethod
from datetime import datetime


# ---- Errors ----

class FSError(Exception): pass
class NotFoundError(FSError): pass
class AlreadyExistsError(FSError): pass
class NotADirectoryError(FSError): pass
class NotEmptyError(FSError): pass
class InvalidPathError(FSError): pass


# ---- Composite: Node / File / Directory ----

class Node(ABC):
    def __init__(self, name, parent):
        self.name = name
        self.parent = parent
        self.created_at = datetime.now()

    @abstractmethod
    def is_directory(self): ...


class File(Node):
    def __init__(self, name, parent, content=""):
        super().__init__(name, parent)
        self.content = content

    def is_directory(self): return False


class Directory(Node):
    def __init__(self, name, parent):
        super().__init__(name, parent)
        self.children = {}

    def is_directory(self): return True

    def add(self, node):
        if node.name in self.children:
            raise AlreadyExistsError(f"'{node.name}' already exists")
        self.children[node.name] = node
        node.parent = self

    def remove(self, name):
        if name not in self.children:
            raise NotFoundError(f"'{name}' not found")
        return self.children.pop(name)


# ---- Path resolution ----

def _split(path):
    if not path:
        raise InvalidPathError("empty path")
    return path.startswith("/"), [p for p in path.split("/") if p]


def resolve(path, cwd, root):
    is_abs, parts = _split(path)
    cur = root if is_abs else cwd
    for part in parts:
        if part == ".": continue
        if part == "..":
            if cur.parent is not None: cur = cur.parent
            continue
        if not cur.is_directory():
            raise NotADirectoryError(f"'{cur.name}' is not a directory")
        if part not in cur.children:
            raise NotFoundError(f"'{part}' not found in '{cur.name}'")
        cur = cur.children[part]
    return cur


def resolve_parent(path, cwd, root):
    is_abs, parts = _split(path)
    if not parts or parts[-1] in (".", ".."):
        raise InvalidPathError(f"invalid path: '{path}'")
    name = parts[-1]
    parent_path = ("/" if is_abs else "") + "/".join(parts[:-1]) or ("/" if is_abs else ".")
    parent = resolve(parent_path, cwd, root)
    if not parent.is_directory():
        raise NotADirectoryError(f"'{parent.name}' is not a directory")
    return parent, name


# ---- FileSystem ----

class FileSystem:
    def __init__(self):
        self.root = Directory("/", None)

    def mkdir(self, path, cwd):
        parent, name = resolve_parent(path, cwd, self.root)
        parent.add(Directory(name, parent))

    def touch(self, path, cwd, content=""):
        parent, name = resolve_parent(path, cwd, self.root)
        parent.add(File(name, parent, content))

    def rm(self, path, cwd, recursive=False):
        node = resolve(path, cwd, self.root)
        if node is self.root:
            raise FSError("cannot remove root")
        if node.is_directory() and node.children and not recursive:
            raise NotEmptyError(f"'{node.name}' is not empty")
        node.parent.remove(node.name)

    def mv(self, src, dst, cwd):
        s = resolve(src, cwd, self.root)
        if s is self.root:
            raise FSError("cannot move root")
        try:
            d = resolve(dst, cwd, self.root)
            if d.is_directory():
                new_parent, new_name = d, s.name
            else:
                raise AlreadyExistsError(f"'{dst}' exists and is not a directory")
        except NotFoundError:
            new_parent, new_name = resolve_parent(dst, cwd, self.root)
        # prevent moving dir into its own subtree
        if s.is_directory():
            w = new_parent
            while w is not None:
                if w is s: raise FSError("cannot move into own subtree")
                w = w.parent
        s.parent.remove(s.name)
        s.name = new_name
        new_parent.add(s)

    def ls(self, path, cwd):
        node = resolve(path, cwd, self.root) if path else cwd
        if not node.is_directory(): return [node.name]
        return sorted(node.children.keys())

    def abspath(self, node):
        if node is self.root: return "/"
        parts = []
        cur = node
        while cur is not None and cur is not self.root:
            parts.append(cur.name)
            cur = cur.parent
        return "/" + "/".join(reversed(parts))


# ---- Session ----

class Session:
    def __init__(self, fs):
        self.fs = fs
        self.cwd = fs.root

    def cd(self, path):
        node = resolve(path, self.cwd, self.fs.root)
        if not node.is_directory():
            raise NotADirectoryError(f"'{path}' is not a directory")
        self.cwd = node

    def pwd(self):
        return self.fs.abspath(self.cwd)


# ---- Command pattern ----

class Command(ABC):
    @abstractmethod
    def execute(self, session, args): ...


class Mkdir(Command):
    def execute(self, s, a):
        if len(a) != 1: raise InvalidPathError("usage: mkdir <path>")
        s.fs.mkdir(a[0], s.cwd)

class Cd(Command):
    def execute(self, s, a):
        if len(a) != 1: raise InvalidPathError("usage: cd <path>")
        s.cd(a[0])

class Pwd(Command):
    def execute(self, s, a): return s.pwd()

class Ls(Command):
    def execute(self, s, a):
        return "  ".join(s.fs.ls(a[0] if a else None, s.cwd))

class Touch(Command):
    def execute(self, s, a):
        if not a: raise InvalidPathError("usage: touch <path>")
        s.fs.touch(a[0], s.cwd)

class Rm(Command):
    def execute(self, s, a):
        r = "-r" in a
        paths = [p for p in a if not p.startswith("-")]
        if not paths: raise InvalidPathError("usage: rm [-r] <path>")
        for p in paths: s.fs.rm(p, s.cwd, recursive=r)

class Mv(Command):
    def execute(self, s, a):
        if len(a) != 2: raise InvalidPathError("usage: mv <src> <dst>")
        s.fs.mv(a[0], a[1], s.cwd)


# ---- Registry / REPL ----

def build_registry():
    r = {}
    r["mkdir"] = Mkdir(); r["cd"] = Cd(); r["ls"] = Ls(); r["pwd"] = Pwd()
    r["touch"] = Touch(); r["rm"] = Rm(); r["mv"] = Mv()
    return r


def run(session, registry, line):
    parts = line.strip().split()
    if not parts: return None
    name, args = parts[0], parts[1:]
    if name not in registry: raise FSError(f"command not found: {name}")
    return registry[name].execute(session, args)


# ---- Demo ----

if __name__ == "__main__":
    fs = FileSystem(); s = Session(fs); reg = build_registry()
    script = [
        "pwd",                    # /
        "mkdir home",
        "mkdir home/aman",
        "cd home/aman",
        "pwd",                    # /home/aman
        "touch readme.txt",
        "mkdir docs",
        "ls",                     # docs  readme.txt
        "cd ..",
        "pwd",                    # /home
        "mv aman/readme.txt aman/docs",
        "ls aman/docs",           # readme.txt
        "rm aman/docs",           # error: not empty
        "rm -r aman/docs",        # ok
        "ls aman",                # (empty-ish)
    ]
    for line in script:
        try:
            print(f"$ {line}")
            out = run(s, reg, line)
            if out is not None: print(out)
        except FSError as e:
            print(f"error: {e}")
```

### What This Covers (for the interviewer)

| Requirement | Where |
|-------------|-------|
| N-ary tree | `Directory.children: dict[str, Node]` |
| `mkdir` (2025) | `FileSystem.mkdir` + `Mkdir` command |
| `cd` (2025) | `Session.cd` + `Cd` command |
| `ls` (2025) | `FileSystem.ls` + `Ls` command |
| `pwd` (2025) | `Session.pwd` + `Pwd` command |
| `create` (2022) | `FileSystem.touch` / `mkdir` |
| `delete` (2022) | `FileSystem.rm` (with empty-dir check) |
| `move` (2022) | `FileSystem.mv` (with subtree-loop guard) |
| Absolute + relative paths | `resolve()` branches on leading `/` |
| `.` and `..` | handled in `resolve()` |
| Error handling | typed `FSError` subclasses, surfaced in REPL |
| Composite pattern | `Node` → `File` / `Directory` |
| Command pattern | `Command` ABC + 7 concrete commands |
| Extensibility: new commands | register one class, no changes to existing code |
| Extensibility: permissions, symlinks, persistence | documented in Step 5 |

### Writing Order (for the 15-20 min budget)

1. **Errors** (~30s) — six one-line subclasses of `FSError`
2. **Node / File / Directory** (~3 min) — Composite base + two concretes
3. **`resolve` + `resolve_parent`** (~3 min) — the trickiest piece; write carefully
4. **`FileSystem`** primitives (~5 min) — `mkdir`, `touch`, `rm`, `mv`, `ls`, `abspath`
5. **`Session`** (~1 min) — `cd`, `pwd`
6. **`Command` ABC + 7 concrete commands** (~4 min) — each is 2-5 lines
7. **Registry + `run` + `__main__` demo** (~2 min) — wire it together, run the script

**Total: ~18 minutes.** Leaves 2-5 mins of slack for debugging, which the interviewer expects.

---

## Step 7: Follow-Up Questions & Answers

> **What to say:** Keep answers under 60 seconds each. Lead with the punchline, then justify.

### Q1. Why Composite instead of flat `dict[str, Node]` keyed by absolute path?

**Flat map pros:** O(1) lookup on absolute path. **Cons:** every `mv`/`rename` rewrites N keys (all descendants); `ls` becomes a prefix scan. Composite makes structural operations O(subtree size) only when you actually touch the subtree, and `ls` is O(children). For a filesystem — where `mv` and `ls` dominate — Composite wins. Flat map is fine for immutable lookups only.

### Q2. How would you make this thread-safe for multiple shell sessions?

Two levels: (a) a single `threading.RLock` on `FileSystem` — simple, kills parallelism; (b) per-`Directory` lock, acquire in path-order to avoid deadlock — more throughput. For Linux-semantics correctness, POSIX uses per-inode locks plus directory `i_rwsem` for rename. I'd start with (a), then shard if contention shows up.

### Q3. How do you handle `..` from root?

`root.parent is None`, so `..` at root is a no-op — matches Linux: `cd /..` stays at `/`. See the `if cur.parent is not None` guard in `resolve`.

### Q4. Why separate `Session.cwd` from `FileSystem`?

The tree is shared across users; `cwd` is per-user. Conflating them means two shells can't be in different directories. Separation also makes the filesystem object easy to serialize (no session state leaks in).

### Q5. How would you add symbolic links?

Add `SymLink(Node)` with `target_path: str`. `resolve` gets a step: "if the current node is a symlink, re-resolve its target relative to its parent, keep a visited set, raise on loops > N hops (Linux caps at 40)." Command classes don't change.

### Q6. How do you prevent `mv /a /a/b/c` (moving into own subtree)?

Walk up from the destination parent via `.parent`; if we ever hit the source, reject. See the `while w is not None` loop in `mv`. O(tree depth).

### Q7. What about `rm` on a non-empty directory?

Default: raise `NotEmptyError` — matches `rmdir`/`rm` POSIX behavior. `-r` flag overrides. In the implementation: `if node.is_directory() and node.children and not recursive: raise`.

### Q8. How would you persist this to disk?

DFS serialize to JSON: `{name, type, children|content, created_at}`. `load()` reconstructs the tree and rewires `parent` pointers on the way down. For durability under concurrent writes, a write-ahead log of commands (mkdir, rm, mv) replayed on load works without needing locks during serialization.

### Q9. How would permissions slot in without rewriting the tree?

Attach a `Permissions(owner, group, mode: int)` object to `Node`. Commands read a `user` from the `Session` and check before every op. No structural change. This is one reason `Session` is a first-class entity — it's where user identity lives.

### Q10. How do you make `ls -R` (recursive)?

Straightforward DFS on `Directory.children` — Composite shines here. Pseudocode: `def ls_r(node, depth): print; if node.is_directory(): for c in sorted(node.children.values()): ls_r(c, depth+1)`.

### Q11. Is path resolution O(depth)?

Yes — one dict lookup per component. Depth is typically < 20 in real filesystems. The N-ary tree keeps each lookup O(1), so total is O(path length). Flat-map alternative is O(1) but pays for it on `mv`/`ls`.

### Q12. What if the user runs `mv file dir` where `dir` is an existing directory?

Move `file` *into* `dir` keeping its name — matches POSIX `mv`. That's the `if d.is_directory(): new_parent, new_name = d, s.name` branch. If `dir` is actually a file, we raise `AlreadyExistsError` (matches `mv: cannot overwrite` unless `-f`).

### Q13. Case sensitivity?

Children are keyed by exact string, so Linux-style case-sensitive. To support case-insensitive (macOS/Windows) swap `Directory.children` to a case-folding dict or normalize names on insert/lookup. Centralized because lookup goes through `resolve`.

### Q14. Why not just use recursion for path resolution?

Iterative is cheaper (no call stack) and handles very deep paths without hitting Python's recursion limit (~1000). For a filesystem, depth can spike with symlinks — iterative is safer.

---

## Closing Statement (say this at the end)

> "The two load-bearing patterns here are Composite — which lets `File` and `Directory` share a tree so path resolution, moves, and recursive operations stay uniform — and Command, which maps 1:1 to the shell verbs the problem asks for and makes adding new commands a single-class change. The N-ary tree lives in `Directory.children` as a dict for O(1) per-component lookup. Separating `Session` from `FileSystem` cleanly supports multi-shell and permissions later. Everything else — symlinks, persistence, concurrency, permissions — plugs into these bones without touching the core."

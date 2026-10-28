#!/usr/bin/env python3
"""Report definitions that nothing in the repositories ever names.

Written because this keeps happening. `assess_identifier` -- the whole
planted-identifier defence -- had exactly one caller and it was the demo
script, so every real investigation scored planted identifiers at face value
while the module header said otherwise. It was found by accident. Then
`_cluster_types_compatible` turned out to be a second, divergent copy of a
merge rule, `FetchDecision.manifest_entry` meant the audit record never said
why a URL was skipped, and `probe_url` plus `verify_egress_country` were a
fail-open proxy check with no caller at all. Four defects of the same shape,
none of which a test would catch, because a test suite only exercises code
somebody remembered to call.

Method: a name-reference census, not a call graph. Every caller in these
packages reaches its target by name, so a definition whose name is loaded
nowhere -- across src, tests, demos, scripts, docs and packaging -- is
unreachable. Two decorators register without a name load (`@register` for
collectors, `@rule` for transliteration rules), so a definition carrying one
is reachable by construction. `@property` is NOT in that set: a property
nobody reads is as dead as a function nobody calls, and exempting it would
hide the exact class of defect this exists to find.

Exit 0 when the only orphans are the ones named in ALLOWED, 1 otherwise.
"""
from __future__ import annotations

import argparse
import ast
import collections
import pathlib
import sys

#: Registry decorators. A definition carrying one is reached through the
#: registry rather than by name.
REGISTRY_DECORATORS = {"register", "rule", "app", "tool", "fixture", "hookimpl"}

#: Extensions worth scanning for a bare name: entry points, docs that tell
#: someone to call a thing, workflow files.
TEXT_SUFFIXES = {".md", ".toml", ".yaml", ".yml", ".sh", ".cfg", ".json",
                 ".txt", ".in"}

SKIP = (".venv", "__pycache__", "/build/", "/dist/", ".egg-info", "/.git/",
        "/out/", "/node_modules/",
        # Generated: the offline bundle embeds every module's source as a
        # base64 payload. Scanning it would count each definition twice and
        # report the embedded copies as reachable, hiding real orphans.
        "attribution_portable.py")

#: Unreachable on purpose, with the reason. Anything here is a deliberate
#: decision, not an oversight -- which is the distinction the gate exists to
#: force someone to make.
ALLOWED: dict[str, str] = {}


class Definitions(ast.NodeVisitor):
    def __init__(self, rel: str, out: dict) -> None:
        self.rel, self.out, self.stack = rel, out, []

    def _record(self, node, kind: str) -> None:
        for dec in node.decorator_list:
            root = dec
            while isinstance(root, (ast.Call, ast.Attribute)):
                root = root.func if isinstance(root, ast.Call) else root.value
            if isinstance(root, ast.Name) and root.id in REGISTRY_DECORATORS:
                return
        owner = self.stack[-1] if self.stack else ""
        self.out.setdefault(node.name, []).append((self.rel, node.lineno, kind, owner))

    def visit_ClassDef(self, node):
        self._record(node, "class")
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node):
        self._record(node, "method" if self.stack else "func")
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef


def _files(roots: list[pathlib.Path]):
    for root in roots:
        for p in root.rglob("*"):
            if p.is_file() and not any(s in str(p) for s in SKIP):
                yield p


def census(roots: list[pathlib.Path]):
    defs: dict[str, list] = {}
    loads: collections.Counter = collections.Counter()
    paths = list(_files(roots))

    for p in paths:
        if p.suffix != ".py" or f"{'/'}src{'/'}" not in str(p):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        Definitions(str(p), defs).visit(tree)

    for p in paths:
        if p.suffix == ".py":
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    loads[node.id] += 1
                elif isinstance(node, ast.Attribute):
                    loads[node.attr] += 1
                elif isinstance(node, ast.ImportFrom):
                    for a in node.names:
                        loads[a.name] += 1
                elif (isinstance(node, ast.Constant)
                      and isinstance(node.value, str)
                      and node.value.isidentifier()):
                    # getattr, entry points, __all__
                    loads[node.value] += 1
        elif p.suffix in TEXT_SUFFIXES:
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for name in defs:
                if name in text:
                    loads[name] += 1

    orphans = {
        name: places for name, places in sorted(defs.items())
        if loads[name] == 0 and not (name.startswith("__") and name.endswith("__"))
    }
    return defs, orphans


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("roots", nargs="*", default=["."],
                    help="repository directories to scan")
    ap.add_argument("--list", action="store_true",
                    help="print every orphan and exit 0, for triage")
    a = ap.parse_args()

    roots = [pathlib.Path(r).resolve() for r in (a.roots or ["."])]
    missing = [r for r in roots if not r.is_dir()]
    if missing:
        print(f"not a directory: {', '.join(str(m) for m in missing)}")
        return 2

    defs, orphans = census(roots)
    print(f"{sum(len(v) for v in defs.values())} definitions, "
          f"{len(orphans)} unreachable")

    unexpected = {n: p for n, p in orphans.items() if n not in ALLOWED}
    for name, places in orphans.items():
        mark = "  allowed:" if name in ALLOWED else "  ORPHAN: "
        for rel, line, kind, owner in places:
            who = f"{owner}.{name}" if owner else name
            print(f"{mark} {kind:6s} {who:42s} {rel}:{line}")
        if name in ALLOWED:
            print(f"            reason: {ALLOWED[name]}")

    if a.list:
        return 0
    if unexpected:
        print(f"\nFAIL: {len(unexpected)} definition(s) nothing ever calls.\n"
              "Decide which it is and do that, rather than leaving it:\n"
              "  - needed, and the absence of a caller IS the defect -> wire it up\n"
              "  - a second copy of a rule that is implemented elsewhere -> delete it;\n"
              "    a divergent duplicate is worse than none, because the dead one\n"
              "    gets 'fixed' and nothing changes\n"
              "  - unreachable on purpose -> add it to ALLOWED with the reason")
        return 1
    print("\nOK: every definition is reachable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

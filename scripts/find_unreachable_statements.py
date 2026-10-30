#!/usr/bin/env python3
"""Statements that can never execute, and branches on a constant condition.

A companion to `find_unreachable.py`, which finds definitions nothing calls.
This asks the different question: within code that IS called, is there anything
that cannot run? Nothing was asking it, and a dead `if True:` wrapper sat in
paytrace's highest-priority business collector -- with an unreachable
`return []` after it -- across several releases.

Neither `ruff` nor `bandit` has a rule for this, so it is checked here.

Generated bundles are skipped: they are assembled from these same sources, so a
finding there is a finding here, reported twice at a line number that exists in
neither file.
"""

from __future__ import annotations

import ast
import pathlib
import sys


def _constant_test(node: ast.expr) -> bool:
    """A literal condition. Strings are excluded: a docstring-like `if "x":` is
    not idiomatic, and treating truthy strings as constants produced noise from
    legitimate `if TYPE_CHECKING:` guards under some toolchains."""
    return isinstance(node, ast.Constant) and not isinstance(node.value, str)


def terminates(stmts: list[ast.stmt]) -> bool:
    """Does this statement list always leave the enclosing frame or loop?

    Deliberately conservative: it says True only where control flow provably
    cannot continue. `try` is not analysed, because an exception can re-enter
    the following statements by way of a handler.
    """
    for s in stmts:
        if isinstance(s, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
            return True
        if isinstance(s, ast.If):
            if _constant_test(s.test):
                if terminates(s.body if s.test.value else s.orelse):
                    return True
            elif s.orelse and terminates(s.body) and terminates(s.orelse):
                return True
        if isinstance(s, ast.With) and terminates(s.body):
            return True
    return False


def audit(roots: list[str]) -> list[str]:
    problems: list[str] = []
    for root in roots:
        for f in sorted(pathlib.Path(root).rglob("src/**/*.py")):
            if "attribution_portable" in f.name:
                continue
            try:
                tree = ast.parse(f.read_text())
            except SyntaxError as e:
                problems.append(f"{f}: will not parse: {e}")
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.If) and _constant_test(node.test):
                    problems.append(
                        f"{f}:{node.lineno}: branch on the constant "
                        f"{node.test.value!r}")
                for field in ("body", "orelse", "finalbody"):
                    stmts = getattr(node, field, None)
                    if not isinstance(stmts, list):
                        continue
                    for i, s in enumerate(stmts[:-1]):
                        if terminates([s]):
                            problems.append(
                                f"{f}:{stmts[i + 1].lineno}: unreachable, after "
                                f"{type(s).__name__} on line {s.lineno}")
                            break
    return problems


def main(argv: list[str]) -> int:
    roots = argv[1:] or ["."]
    problems = audit(roots)
    for p in problems:
        print(p)
    if problems:
        print(f"\n{len(problems)} unreachable-code finding(s)")
        return 1
    print("no unreachable statements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

"""Version reporting.

Reads each package's ``__version__`` constant rather than installation metadata.

``importlib.metadata.version()`` returns a fallback for a source tree that has
not been pip-installed as a distribution, which made a perfectly valid
source-tree run report a version divergence that did not exist. The source
constant is the truth; distribution metadata is derived from it at build time.
"""

from __future__ import annotations

import importlib

#: Import name -> distribution name.
PACKAGES: dict[str, str] = {
    "attribution_graph": "attribution-graph",
    "paytrace": "paytrace",
    "handle_correlation": "handle-correlation",
    "attribution_suite": "attribution-suite",
}


def versions() -> dict[str, str]:
    """Every component's version, keyed by import name.

    Keyed by import name rather than distribution name because that is what
    callers already index by, and because the import name is what actually
    resolved -- a distribution name says nothing about which code was loaded.
    """
    out: dict[str, str] = {}
    for mod in PACKAGES:
        try:
            out[mod] = getattr(importlib.import_module(mod), "__version__", "unknown")
        except ImportError:
            out[mod] = "not installed"
    return out


def _line(version: str) -> str:
    """MAJOR.MINOR — the compatibility line the `==X.Y.*` pins declare."""
    parts = version.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else version


def aligned() -> bool:
    """Whether every installed component is on the same MAJOR.MINOR line.

    Compared on MAJOR.MINOR, not the exact version. The packages pin each
    other at `==2.0.*`, which explicitly declares patch releases compatible --
    so demanding an identical patch version was stricter than the contract it
    enforced, and a paytrace 2.0.1 bug-fix beside 2.0.0 siblings would have made
    `attribution run` refuse to start for every user.

    A MINOR or MAJOR skew still fails: those are where evidence semantics can
    change between components.
    """
    seen = {_line(v) for v in versions().values()
            if v not in ("not installed", "unknown")}
    return len(seen) <= 1


def banner() -> str:
    """Human-readable version block for ``attribution version``."""
    v = versions()
    lines = [f"{PACKAGES[mod]:<22} {ver}" for mod, ver in v.items()]
    if not aligned():
        lines.append("")
        lines.append("WARNING: component versions diverge. These packages are "
                     "released together; a mismatch means one was upgraded "
                     "without the others.")
    return "\n".join(lines)

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


def aligned() -> bool:
    """Whether every installed component reports the same version.

    Versions move together across the four packages: bumping one and not the
    rest is the easiest mistake to make in this layout, so the suite's own test
    asserts this.
    """
    seen = {v for v in versions().values() if v not in ("not installed", "unknown")}
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

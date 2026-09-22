"""attribution-suite: one install and one CLI over the three attribution packages.

The packages are separate on purpose. ``attribution-graph`` is a pure inference
library with no network I/O, which is what makes its scoring auditable;
``paytrace`` carries the collectors and their dependencies;
``handle-correlation`` is independent of both. Someone who only wants the scoring
model should not have to install an HTTP client, and someone building their own
collectors should not inherit ours.

This package exists for the case where you want all of it: ``pip install
attribution-suite`` and a single ``attribution`` command that runs the whole
chain — collect, score, resolve, verify, report — with one case file.
"""

from ._version import __version__  # noqa: F401
from .investigator import (
    Intent,
    Investigation,
    Investigator,
    Refused,
    ask,
    classify,
)
from .runner import SuiteResult, run_case
from .version import PACKAGES, versions

__all__ = [
    "run_case", "SuiteResult", "versions", "PACKAGES",
    "Investigator", "Investigation", "Intent", "Refused", "ask", "classify",
    "__version__",
]

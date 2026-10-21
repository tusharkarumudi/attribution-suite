"""Where this interpreter lives, and what that means for install advice.

A pipx install, a virtualenv and a system Python each have a different correct
state and a different remedy. Advice that assumes one of them is wrong for the
other two -- `pip install "attribution-suite[llm]"` does nothing useful to
someone who installed with pipx, and plain `pip install` is refused outright on
a system Python (PEP 668).

Kept out of ``cli`` so the runtime paths that raise these messages
(``investigator``, ``mcp_server``) can use them without importing the CLI.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: The error Debian, Ubuntu, Fedora and Homebrew Python produce for a
#: system-wide ``pip install``. Quoted verbatim so that a user searching for
#: what they saw finds the remedy.
PEP668 = "externally-managed-environment"


def install_mode() -> str:
    """Return "pipx", "venv" or "system" for the running interpreter."""
    prefix = Path(sys.prefix).resolve()
    if "pipx" in prefix.parts and "venvs" in prefix.parts:
        return "pipx"
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return "venv"
    return "system"


def extra_hint(extra: str, package: str) -> str:
    """The command that actually installs ``extra`` here.

    ``extra`` is the name of the optional-dependency group ("llm", "mcp");
    ``package`` is the distribution it provides, which is what pipx injects --
    pipx adds packages to an existing environment and has no notion of an
    extra after the fact.
    """
    mode = install_mode()
    if mode == "pipx":
        return f"pipx inject attribution-suite {package}"
    cmd = f'pip install "attribution-suite[{extra}]"'
    if mode == "system":
        return (f"{cmd}\n"
                f"    ...which a system Python refuses ({PEP668}). Install the "
                f"tool with pipx instead:\n"
                f'    pipx install "attribution-suite[{extra}]"')
    return cmd

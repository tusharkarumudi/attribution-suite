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


def _mode_from_path() -> str:
    """Classify by path alone, for a prefix that cannot be stat'd.

    Weaker than the metadata marker -- it is the test that missed an
    unusually-placed PIPX_HOME in the first place -- but it touches no
    filesystem, so it always answers.
    """
    parts = Path(sys.prefix).parts
    if "pipx" in parts and "venvs" in parts:
        return "pipx"
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return "venv"
    return "system"


def install_mode() -> str:
    """Return "pipx", "venv" or "system" for the running interpreter.

    pipx is detected by the `pipx_metadata.json` it writes into every venv it
    manages, not by looking for "pipx" in the path. The path test failed
    wherever `PIPX_HOME` is somewhere unusual -- a directory called `pipxhome`
    has no part equal to "pipx" -- and a misdetected pipx install is told to
    run `pip install`, which silently does nothing to the pipx venv.
    """
    try:
        prefix = Path(sys.prefix).resolve()
        marker = (prefix / "pipx_metadata.json").is_file()
    except OSError:
        # `Path.is_file()` does NOT swallow every OSError: pathlib ignores
        # ENOENT, ENOTDIR, EBADF and ELOOP, and re-raises the rest -- so an
        # unreadable prefix raises PermissionError rather than answering
        # "no marker here". `install_mode` is called from `investigator` and
        # `mcp_server`, not just the doctor, so that aborted an investigation
        # over a directory the process was never entitled to read in the first
        # place. A prefix we cannot stat tells us nothing; fall through to the
        # path test, which needs no filesystem access at all.
        return _mode_from_path()
    if marker:
        return "pipx"
    if "pipx" in prefix.parts and "venvs" in prefix.parts:
        return "pipx"
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return "venv"
    return "system"


def pipx_metadata() -> dict:
    """What pipx recorded about this install, or {} when not under pipx."""
    import json

    path = Path(sys.prefix).resolve() / "pipx_metadata.json"
    try:
        return json.loads(path.read_text()).get("main_package") or {}
    except (OSError, ValueError, AttributeError):
        return {}


def sibling_apps_hint() -> str:
    """How to expose the dependency packages' commands.

    `pipx install attribution-suite` installs all three sibling packages --
    they are ordinary dependencies -- but exposes only THIS package's console
    scripts. `paytrace`, `paytrace-index` and `handlecorr` belong to the
    dependencies, so they are absent from PATH, and the doctor used to report
    that as a bare "NOT ON PATH" beside a list that reads like missing
    packages. One user reasonably concluded the install had failed and
    injected packages that were already there.
    """
    if install_mode() != "pipx":
        return ""
    # Returns the command whenever this is a pipx install. Whether it still
    # NEEDS running is not decided here: `include_dependencies` is set by
    # `pipx install --include-deps` and stays False after a
    # `pipx inject --include-apps`, so a metadata check keeps advising a remedy
    # that has already been applied -- which is the same "fix what is not
    # broken" failure this whole change set exists to remove, reached by
    # another route. The caller gates on what is actually on PATH, because that
    # is the thing the advice is about and it is directly observable.
    #
    # `pipx inject --include-apps`, not `pipx install --include-deps`: the
    # packages are already present, so there is nothing to reinstall, and
    # --include-deps also puts `httpx` and `idna` on PATH, which nobody asked
    # for. The in-section hint and this one must agree -- two spellings of the
    # same remedy read as two different problems.
    return ("pipx inject attribution-suite paytrace handle-correlation "
            "--include-apps")


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

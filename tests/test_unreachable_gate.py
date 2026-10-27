"""The gate that catches the next one.

`assess_identifier` -- the entire planted-identifier defence -- had one caller
and it was the demo script, so real investigations scored planted identifiers
at face value for four releases. It was found by accident. A test suite cannot
find that class of defect, because a suite only exercises what somebody
remembered to call; the dead code is dead in the tests too.

So the gate is tested like any other deliverable, and the first test here is
the one that matters: a gate that cannot fail is worse than no gate, because
it is read as assurance.
"""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "find_unreachable.py"


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, timeout=300)


def _repo(tmp_path, body: str, name: str = "mod.py") -> Path:
    pkg = tmp_path / "src" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / name).write_text(body)
    return tmp_path


def test_the_script_ships_with_the_package():
    assert SCRIPT.exists(), f"{SCRIPT} is missing; VERIFY.sh runs it"


def test_it_fails_on_a_function_nobody_calls(tmp_path):
    r = _run(str(_repo(tmp_path, "def never_called(x):\n    return x\n")))
    assert r.returncode == 1, r.stdout
    assert "never_called" in r.stdout
    assert "FAIL" in r.stdout


def test_it_fails_on_a_property_nobody_reads(tmp_path):
    """`@property` is deliberately not treated as a registry decorator.

    Exempting decorated definitions wholesale was the first version of this,
    and it hid every dead accessor -- which is most of what turns up.
    """
    body = ("class C:\n"
            "    @property\n"
            "    def unread(self):\n"
            "        return 1\n")
    r = _run(str(_repo(tmp_path, body)))
    assert r.returncode == 1
    assert "C.unread" in r.stdout


def test_a_registered_collector_is_not_an_orphan(tmp_path):
    """Collectors are reached through `@register`, never by name. Reporting
    them would bury the real findings under thirty false ones."""
    body = ("def register(c):\n"
            "    return c\n"
            "\n"
            "@register\n"
            "class Rdap:\n"
            "    pass\n")
    r = _run(str(_repo(tmp_path, body)))
    assert "Rdap" not in r.stdout, r.stdout
    assert r.returncode == 0


def test_a_name_used_only_in_a_docstring_or_toml_counts_as_reached(tmp_path):
    """An entry point names its target in a string, and a guide may be the
    only thing that tells a reader to call something. Both are callers."""
    root = _repo(tmp_path, "def documented_entry(argv=None):\n    return 0\n")
    (root / "pyproject.toml").write_text(
        '[project.scripts]\nthing = "pkg.mod:documented_entry"\n')
    r = _run(str(root))
    assert r.returncode == 0, r.stdout


def test_a_name_loaded_only_by_a_test_counts_as_reached(tmp_path):
    """Deliberate. A helper exercised only by tests is a judgement call about
    test design, not an unreachable definition, and conflating the two makes
    the gate too noisy to keep."""
    root = _repo(tmp_path, "def helper():\n    return 1\n")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text(
        "from pkg.mod import helper\n\n\ndef test_h():\n    assert helper()\n")
    r = _run(str(root))
    assert r.returncode == 0, r.stdout


def test_dunders_are_not_reported(tmp_path):
    body = ("class C:\n"
            "    def __init__(self):\n"
            "        self.x = 1\n"
            "\n"
            "    def __repr__(self):\n"
            "        return 'C'\n")
    r = _run(str(_repo(tmp_path, body)))
    assert r.returncode == 0, r.stdout


def test_list_mode_reports_without_failing(tmp_path):
    r = _run("--list", str(_repo(tmp_path, "def never_called():\n    return 1\n")))
    assert r.returncode == 0
    assert "never_called" in r.stdout


def test_a_missing_directory_is_an_error_not_a_pass(tmp_path):
    """A gate that silently passes when pointed at nothing is the vacuous
    case: VERIFY.sh would report it green forever."""
    r = _run(str(tmp_path / "does-not-exist"))
    assert r.returncode == 2
    assert "not a directory" in r.stdout


@pytest.mark.slow
def test_the_shipped_repositories_have_no_orphans():
    """The standing assertion. If this fails, read the output and decide:
    wire it up, delete it, or record why it is unreachable on purpose."""
    base = SCRIPT.resolve().parents[2]
    repos = [str(base / r) for r in
             ("attribution-graph", "paytrace", "handle-correlation",
              "attribution-suite") if (base / r).is_dir()]
    if len(repos) < 4:
        pytest.skip("not running from the four-repository layout")
    r = _run(*repos)
    assert r.returncode == 0, r.stdout

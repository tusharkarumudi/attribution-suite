"""Suite wiring."""

import sys

import pytest

from attribution_suite import PACKAGES, versions
from attribution_suite.cli import main


def test_all_component_packages_import():
    v = versions()
    for p in PACKAGES:
        assert v[p] != "not installed", p


def test_versions_are_aligned():
    v = versions()
    # MAJOR.MINOR line, not an identical version: the ==2.0.* pins declare
    # patch releases compatible, and paytrace ships 2.0.1 beside 2.0.0.
    lines = {".".join(x.split(".")[:2]) for x in (v[p] for p in PACKAGES)}
    assert len(lines) == 1, "component version lines diverge: {v}"


def test_version_command_runs(capsys):
    assert main(["version"]) == 0
    assert "attribution-graph" in capsys.readouterr().out


def test_run_refuses_a_case_without_authorization(tmp_path, capsys):
    c = tmp_path / "bad.yaml"
    c.write_text("case_ref: X\nseeds: [domain:a.example]\n")
    assert main(["run", "--case", str(c), "--out", str(tmp_path / "o")]) == 2
    assert "authorization" in capsys.readouterr().err


def test_registries_delegates_to_component_cli(capsys):
    assert main(["registries", "--jurisdiction", "GB"]) == 0
    assert "Companies House" in capsys.readouterr().out


def test_install_mode_distinguishes_pipx_venv_and_system(monkeypatch):
    """A pipx venv, a plain venv and a system Python are not the same thing.

    Each one has a different correct state and a different remedy, so a check
    that cannot tell them apart gives the wrong instruction to two of three.
    """
    from attribution_suite.cli import install_mode

    cases = {
        "/root/.local/share/pipx/venvs/attribution-suite": "pipx",
        "/home/u/.local/pipx/venvs/attribution-suite": "pipx",
        "/home/u/ncs-venv": "venv",
        "/usr": "system",
    }
    for prefix, expected in cases.items():
        monkeypatch.setattr(sys, "prefix", prefix)
        monkeypatch.setattr(sys, "base_prefix", "/usr")
        assert install_mode() == expected, prefix


def test_doctor_does_not_fail_a_pipx_install_over_dependency_commands(
        monkeypatch, capsys):
    """pipx exposes only the apps of the package it installed.

    `paytrace` and `handlecorr` belong to the dependencies, so pipx leaves them
    off PATH by design. Reporting that as a broken install sends a correctly
    installed user looking for a fault that is not there.
    """
    import shutil

    from attribution_suite.cli import main

    monkeypatch.setattr(
        sys, "prefix", "/root/.local/share/pipx/venvs/attribution-suite")
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    real = shutil.which
    monkeypatch.setattr(
        shutil, "which",
        lambda c, *a, **k: None if c in ("paytrace", "paytrace-index",
                                         "handlecorr") else real(c, *a, **k))

    main(["doctor"])
    out = capsys.readouterr().out
    commands = out.split("COMMANDS")[1].split("LLM PLANNING")[0]
    for cmd in ("paytrace", "paytrace-index", "handlecorr"):
        assert f"x  {cmd}" not in commands, f"{cmd} reported as a fault"
        assert f"-- {cmd}" in commands, f"{cmd} not reported at all"
    assert "pipx inject attribution-suite" in out, "no remedy offered"
    # The exit code is not asserted: doctor also makes a live request, and this
    # test must not depend on the network to prove something about PATH.


def test_doctor_explains_pep_668_on_a_system_python(monkeypatch, capsys):
    """'externally-managed-environment' is the single commonest install failure.

    Debian, Ubuntu, Fedora and Homebrew all refuse a system-wide pip install.
    The error names venv and pipx generically; doctor names the command for
    THIS tool.
    """
    from attribution_suite.cli import main

    monkeypatch.setattr(sys, "prefix", "/usr")
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    main(["doctor"])
    out = capsys.readouterr().out
    assert "externally-managed-environment" in out
    assert "pipx install" in out


def test_extra_hint_matches_the_install_mode(monkeypatch):
    """An optional extra is installed differently in each environment.

    `pip install "attribution-suite[llm]"` does nothing for a pipx user: pipx
    adds packages to an existing environment and has no notion of an extra
    after the fact. On a system Python the pip form is refused outright, so a
    message that prints only that command sends the user into the error they
    already hit.
    """
    from attribution_suite.install import extra_hint

    monkeypatch.setattr(
        sys, "prefix", "/root/.local/share/pipx/venvs/attribution-suite")
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    assert extra_hint("llm", "anthropic") == (
        "pipx inject attribution-suite anthropic")

    monkeypatch.setattr(sys, "prefix", "/home/u/ncs-venv")
    assert extra_hint("llm", "anthropic") == (
        'pip install "attribution-suite[llm]"')

    monkeypatch.setattr(sys, "prefix", "/usr")
    system = extra_hint("mcp", "mcp")
    assert "externally-managed-environment" in system
    assert 'pipx install "attribution-suite[mcp]"' in system


def test_help_names_the_command_the_user_typed(monkeypatch, capsys):
    """`attribution-suite --help` opening with "usage: attribution run" sends a
    first-time reader looking for a subcommand they never typed."""
    from attribution_suite.cli import main

    monkeypatch.setattr(sys, "argv", ["attribution-suite", "--help"])
    with pytest.raises(SystemExit):
        main(["run", "--help"])
    out = capsys.readouterr().out
    assert out.startswith("usage: attribution-suite "), out.splitlines()[0]
    assert "attribution-suite run" not in out

    monkeypatch.setattr(sys, "argv", ["attribution", "run", "--help"])
    with pytest.raises(SystemExit):
        main(["run", "--help"])
    assert capsys.readouterr().out.startswith("usage: attribution run")


def test_every_run_option_carries_help_text():
    """Seventeen bare flags is how `--domains` stayed invisible after it shipped.

    Inspects the real parser rather than its rendered output: argparse wraps
    long lines, so reading `--help` as text reports documented options as
    undocumented.
    """
    import argparse

    from attribution_suite.cli import build_parser

    ap = build_parser("attribution")
    subs = [a for a in ap._actions
            if isinstance(a, argparse._SubParsersAction)]
    assert subs, "no subcommands found; the parser shape changed"
    run = subs[0].choices["run"]
    missing = [
        "/".join(a.option_strings) for a in run._actions
        if a.option_strings and a.help in (None, "")
        and a.help is not argparse.SUPPRESS
    ]
    assert not missing, f"options with no help text: {missing}"


def test_the_declared_minimums_match_the_apis_this_package_calls():
    """A pin is a compatibility statement, and these two are load-bearing.

    The runner calls `Engine.run(before_resolve=...)` and passes
    `sharing_domains`/`holder_counts` into `assess_identifier`. Under the old
    `>=2.0.1` pins, `pipx upgrade attribution-suite` alone resolved to siblings
    without those parameters, and every run died with a TypeError that named
    an internal keyword argument.
    """
    import inspect
    import tomllib
    from pathlib import Path

    from attribution_graph import Engine
    from paytrace.adversarial import assess_identifier

    assert "before_resolve" in inspect.signature(Engine.run).parameters, \
        "the installed attribution-graph is older than the pin requires"
    params = inspect.signature(assess_identifier).parameters
    for name in ("sharing_domains", "holder_counts"):
        assert name in params, f"the installed paytrace has no {name}"

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    deps = tomllib.loads(pyproject.read_text())["project"]["dependencies"]

    # Compared as versions, not as literal strings. This asserted
    # `"attribution-graph>=2.0.7" in joined` and so failed on the next bump --
    # every release, for a pin that had only moved FORWARD. A test that has to
    # be edited whenever the thing it guards is legitimately updated gets
    # edited without being read, which is how it stops guarding anything.
    from packaging.requirements import Requirement
    from packaging.version import Version

    floors = {}
    for raw in deps:
        req = Requirement(raw)
        for spec in req.specifier:
            if spec.operator == ">=":
                floors[req.name] = Version(spec.version)

    #: The release that introduced the API named beside it. A floor BELOW this
    #: resolves to a sibling without that API, and the failure is a TypeError
    #: naming an internal keyword argument rather than a resolver error.
    REQUIRED = {
        "attribution-graph": (Version("2.0.7"), "Engine.run(before_resolve=)"),
        "paytrace": (Version("2.0.25"),
                     "assess_identifier(sharing_domains=, holder_counts=)"),
    }
    for name, (needed, why) in REQUIRED.items():
        assert name in floors, f"{name} has no >= floor; {why} is unguarded"
        assert floors[name] >= needed, (
            f"{name}>={floors[name]} is below {needed}, which is where {why} "
            "landed: that resolves to a sibling without it")

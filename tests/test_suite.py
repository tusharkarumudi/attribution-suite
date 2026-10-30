"""Suite wiring."""

import sys

import pytest

from attribution_suite import PACKAGES, versions
from attribution_suite.cli import main


@pytest.fixture
def pipx_prefix(tmp_path, monkeypatch):
    """A pipx-shaped prefix inside tmp_path, with the marker pipx writes.

    These tests used to point `sys.prefix` at the literal
    `/root/.local/share/pipx/venvs/attribution-suite`. That is a real location:
    on a machine where it exists and the test user cannot read it,
    `Path.is_file()` raises PermissionError rather than returning False, and
    six tests failed on a developer's machine while passing in a root
    container. A unit test must not depend on what happens to be on the disk
    outside tmp_path.
    """
    venv = tmp_path / "pipx" / "venvs" / "attribution-suite"
    venv.mkdir(parents=True)
    (venv / "pipx_metadata.json").write_text(
        '{"main_package": {"include_dependencies": false}}')
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    return venv


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
    from attribution_suite.install import _mode_from_path

    # The path classifier is pure, so these can stay as literal paths: nothing
    # here touches the filesystem. `install_mode` itself is exercised against
    # real directories under tmp_path by the two tests below, which is where
    # the metadata marker matters.
    cases = {
        "/root/.local/share/pipx/venvs/attribution-suite": "pipx",
        "/home/u/.local/pipx/venvs/attribution-suite": "pipx",
        "/home/u/ncs-venv": "venv",
        "/usr": "system",
    }
    for prefix, expected in cases.items():
        monkeypatch.setattr(sys, "prefix", prefix)
        monkeypatch.setattr(sys, "base_prefix", "/usr")
        assert _mode_from_path() == expected, prefix


def test_doctor_does_not_fail_a_pipx_install_over_dependency_commands(
        pipx_prefix, monkeypatch, capsys):
    """pipx exposes only the apps of the package it installed.

    `paytrace` and `handlecorr` belong to the dependencies, so pipx leaves them
    off PATH by design. Reporting that as a broken install sends a correctly
    installed user looking for a fault that is not there.
    """
    import shutil

    from attribution_suite.cli import main

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


def test_extra_hint_matches_the_install_mode(pipx_prefix, tmp_path, monkeypatch):
    """An optional extra is installed differently in each environment.

    `pip install "attribution-suite[llm]"` does nothing for a pipx user: pipx
    adds packages to an existing environment and has no notion of an extra
    after the fact. On a system Python the pip form is refused outright, so a
    message that prints only that command sends the user into the error they
    already hit.
    """
    from attribution_suite.install import extra_hint

    assert extra_hint("llm", "anthropic") == (
        "pipx inject attribution-suite anthropic")

    # Outside the pipx tree: a path under `.../pipx/venvs/` is classified pipx
    # by the path test, whatever it is called.
    plain = tmp_path / "plain-venv"
    plain.mkdir()
    monkeypatch.setattr(sys, "prefix", str(plain))
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


def test_install_mode_finds_pipx_by_its_metadata_not_by_the_path(
        monkeypatch, tmp_path):
    """The path test alone misses any unusually-placed PIPX_HOME.

    `install_mode` used to look for a path part equal to "pipx". A user whose
    PIPX_HOME is `~/pipxhome` has no such part -- "pipxhome" != "pipx" -- so
    their pipx install was classified `venv` and told to run `pip install`,
    which a pipx venv has no pip to run. pipx writes `pipx_metadata.json` into
    every venv it manages, so that file is the fact and the path is a fallback.
    """
    from attribution_suite.install import install_mode

    odd = tmp_path / "pipxhome" / "venvs" / "attribution-suite"
    odd.mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(odd))
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    assert install_mode() == "venv", "a venv without the marker is a venv"

    (odd / "pipx_metadata.json").write_text("{}")
    assert install_mode() == "pipx", "the marker was ignored"


def test_the_doctor_withholds_a_remedy_that_has_already_been_applied(
        pipx_prefix, monkeypatch, capsys):
    """Advice to run a command that has already been run reads as a failure.

    The first attempt gated this on pipx's `include_dependencies` flag, which
    is wrong in the commonest case: that flag is set by
    `pipx install --include-deps` and stays False after a
    `pipx inject --include-apps`, so someone who ran the remedy the doctor
    printed was told to run it again, with all three commands reported `ok`
    three lines above. Caught by running the printed command against a real
    pipx install.

    What the advice is about is what is on PATH, so that is what decides.
    """
    import shutil

    from attribution_suite.cli import main

    siblings = ("paytrace", "paytrace-index", "handlecorr")
    real_which = shutil.which

    # All three exposed -- the state after the remedy -- and no metadata saying
    # so, exactly as `pipx inject --include-apps` leaves it.
    monkeypatch.setattr(
        shutil, "which",
        lambda c, *a, **k: ("/bin/" + c if c in siblings
                            else real_which(c, *a, **k)))
    main(["doctor"])
    out = capsys.readouterr().out
    assert "--include-apps" not in out.split("TO FIX")[-1], (
        "advised a remedy whose effect is already visible on PATH")

    # One missing is enough to be worth saying.
    monkeypatch.setattr(
        shutil, "which",
        lambda c, *a, **k: (None if c == "handlecorr"
                            else "/bin/" + c if c in siblings
                            else real_which(c, *a, **k)))
    main(["doctor"])
    assert "--include-apps" in capsys.readouterr().out.split("TO FIX")[-1], (
        "a missing sibling command went unremedied")


def test_the_sibling_hint_survives_unreadable_pipx_metadata(
        monkeypatch, tmp_path):
    """A metadata file in a format this code does not know must not raise.

    `doctor` is what someone runs when things are already wrong, so it is the
    last command that may crash on an unexpected file.
    """
    from attribution_suite.install import pipx_metadata, sibling_apps_hint

    venv = tmp_path / "venvs" / "attribution-suite"
    venv.mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    for content in ("not json at all", "[]", '{"main_package": null}', ""):
        (venv / "pipx_metadata.json").write_text(content)
        assert pipx_metadata() == {}, content
        assert "--include-apps" in sibling_apps_hint(), content


def test_the_doctor_ends_with_every_command_it_wants_run(pipx_prefix, monkeypatch, capsys):
    """The hints have to arrive as a block the reader can copy.

    Scattered one per section, the reader assembles them -- and the one that
    matters most, that the sibling commands are absent while the packages are
    installed, read as a failed install. Each command is printed with the
    reason beside it, and the install method is named so a reader who pasted
    `pip install` into a pipx venv can see why nothing happened.
    """
    import importlib.util
    import shutil

    from attribution_suite.cli import main

    real_which = shutil.which
    monkeypatch.setattr(
        shutil, "which",
        lambda c, *a, **k: None if c in ("paytrace", "paytrace-index",
                                         "handlecorr") else real_which(c, *a, **k))
    # Force both optional libraries absent so every remedy appears at once.
    # They are detected differently on purpose: `mcp` is never imported by the
    # doctor, so a spec lookup is enough, while `anthropic` is reported by the
    # import the runtime itself performs -- which is broken here the way Python
    # reports a halted import.
    real_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec",
        lambda n, *a, **k: None if n == "mcp" else real_spec(n, *a, **k))
    monkeypatch.setitem(sys.modules, "anthropic", None)

    main(["doctor"])
    out = capsys.readouterr().out
    assert "TO FIX" in out, "the commands were never collected"
    fix = out.split("TO FIX")[1]
    assert "install method detected: pipx" in fix
    for expected in ("pipx inject attribution-suite paytrace handle-correlation",
                     "pipx inject attribution-suite anthropic",
                     "pipx inject attribution-suite mcp"):
        assert expected in fix, f"missing remedy: {expected}"
    # pip, in a pipx venv, is the advice that sent the user in circles.
    assert "pip install" not in fix, "pip advice offered to a pipx install"
    # Every line carries why it is there.
    for line in (ln for ln in fix.splitlines() if "pipx inject" in ln):
        assert "#" in line, f"remedy with no reason: {line!r}"


def test_a_system_python_remedy_keeps_its_explanation_with_its_command(
        monkeypatch, capsys):
    """One remedy spans several lines; the aligned block must not break it.

    On a system Python `extra_hint` returns the pip command, the PEP 668
    refusal it will produce, and the pipx form that works. Padding only the
    first line to the column width put the trailing `# reason` after the last
    continuation line, where it described the wrong command.
    """
    import importlib.util

    from attribution_suite.cli import main

    monkeypatch.setattr(sys, "prefix", "/usr")
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    real_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec",
        lambda n, *a, **k: None if n == "mcp" else real_spec(n, *a, **k))

    main(["doctor"])
    fix = capsys.readouterr().out.split("TO FIX")[1]
    head = next(ln for ln in fix.splitlines() if "pip install" in ln
                and "attribution-suite[mcp]" in ln and "pipx" not in ln)
    assert head.rstrip().endswith("# the MCP server's SDK"), head
    assert "externally-managed-environment" in fix


def test_the_doctor_notices_a_host_skill_copy_left_behind_by_an_upgrade(
        monkeypatch, tmp_path, capsys):
    """`skill --install` copies; an upgrade does not revisit the copy.

    So the host keeps reading whatever SKILL.md it was given, which may predate
    the tool by several releases. The symptom -- a model applying superseded
    rules, calling a band a probability because an older skill let it -- looks
    like a model fault, and nothing in the install reports it. Nothing in the
    copy records a version either, so the content is what gets compared.
    """
    from attribution_suite import cli

    host = tmp_path / "skills" / "ad-attribution"
    host.mkdir(parents=True)
    for f in cli.SKILL_DIR.rglob("*"):
        if f.is_file() and "__pycache__" not in f.parts:
            target = host / f.relative_to(cli.SKILL_DIR)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f.read_bytes())
    monkeypatch.setattr(cli, "DEFAULT_SKILL_HOME", host)

    cli.main(["doctor"])
    fresh = capsys.readouterr().out
    assert "ok installed for a host" in fresh
    assert "STALE" not in fresh, "a byte-identical copy reported as stale"

    # What an upgrade leaves behind: the host's copy is the older text.
    (host / "SKILL.md").write_text("rules from an older release\n")
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "STALE" in out, "a superseded host copy went unreported"
    assert "attribution skill --install --force" in out, "no refresh command"

    # A file dropped from the copy registers because the byte stream shortens.
    (host / "SKILL.md").write_bytes(
        (cli.SKILL_DIR / "SKILL.md").read_bytes())
    (host / "mcp.json").unlink()
    cli.main(["doctor"])
    assert "STALE" in capsys.readouterr().out, "a missing file went unreported"


def test_the_doctor_marks_x_only_on_what_actually_blocks_a_run(
        pipx_prefix, monkeypatch, capsys):
    """The legend under TO FIX promises `x` means blocking and `--` means optional.

    A correct pipx install with no optional extras and no API key is fully
    usable, so every optional gap in it must read `--`. The anthropic SDK line
    printed `x` there while the sentence directly below it said the fixed-rule
    planner is "a supported mode, not a failure" -- so the marker, the prose and
    the legend disagreed, and the reader had no way to tell which to believe.
    """
    import importlib.util
    import shutil

    from attribution_suite.cli import main

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    real_which, real_spec = shutil.which, importlib.util.find_spec
    monkeypatch.setattr(
        shutil, "which",
        lambda c, *a, **k: None if c in ("paytrace", "paytrace-index",
                                         "handlecorr") else real_which(c, *a, **k))
    monkeypatch.setattr(
        importlib.util, "find_spec",
        lambda n, *a, **k: None if n == "mcp" else real_spec(n, *a, **k))
    monkeypatch.setitem(sys.modules, "anthropic", None)

    main(["doctor"])
    out = capsys.readouterr().out
    # NETWORK makes a live request; a sandbox without egress legitimately
    # reports `x` there, and that is not what this test is about.
    body = out.split("NETWORK")[0]
    offenders = [ln for ln in body.splitlines()
                 if ln.startswith(("  x ", "  x  ", "  x    "))]
    assert not offenders, (
        "optional gaps marked as blocking, contradicting the TO FIX legend:\n"
        + "\n".join(offenders))

    # With a key set, the same missing SDK IS an inconsistency: the key says
    # the adaptive planner was wanted and it cannot run.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    main(["doctor"])
    planning = capsys.readouterr().out.split("LLM PLANNING")[1].split("AGENT")[0]
    assert "x" in planning, "a key set with no SDK went unflagged"


def test_install_mode_survives_a_prefix_it_cannot_read(monkeypatch, tmp_path):
    """`Path.is_file()` does not swallow PermissionError.

    pathlib ignores ENOENT, ENOTDIR, EBADF and ELOOP and re-raises everything
    else, so a prefix the process may not stat raised instead of answering
    "no marker here". `install_mode` is called from `investigator` and
    `mcp_server`, not only the doctor, so that aborted an investigation over a
    directory the process was never entitled to read.

    Reported from a real machine: the suite's own tests pointed `sys.prefix`
    at `/root/.local/share/pipx/venvs/attribution-suite`, which existed and was
    unreadable there, and six of them failed with EACCES while passing in a
    root container.
    """
    import pathlib

    from attribution_suite.install import install_mode

    venv = tmp_path / "pipx" / "venvs" / "attribution-suite"
    venv.mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setattr(sys, "base_prefix", "/usr")

    real_is_file = pathlib.Path.is_file

    def denied(self, *a, **kw):
        if self.name == "pipx_metadata.json":
            raise PermissionError(13, "Permission denied", str(self))
        return real_is_file(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "is_file", denied)

    # It must answer, and answer correctly from the path alone.
    assert install_mode() == "pipx"

    # And a venv that is not pipx-shaped still classifies as a venv rather
    # than falling all the way through to "system", which would print
    # `pip install` advice refused by PEP 668.
    plain = tmp_path / "plain-venv"
    plain.mkdir()
    monkeypatch.setattr(sys, "prefix", str(plain))
    assert install_mode() == "venv"


def test_the_doctor_runs_when_the_prefix_cannot_be_read(monkeypatch, capsys):
    """The command people run when things are broken must not itself break."""
    import pathlib

    from attribution_suite.cli import main

    real_is_file = pathlib.Path.is_file

    def denied(self, *a, **kw):
        if self.name == "pipx_metadata.json":
            raise PermissionError(13, "Permission denied", str(self))
        return real_is_file(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "is_file", denied)
    main(["doctor"])
    assert "INSTALL" in capsys.readouterr().out

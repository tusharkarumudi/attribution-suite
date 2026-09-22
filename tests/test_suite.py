"""Suite wiring."""


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

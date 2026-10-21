"""The agentic-host surface: MCP tools and the skill file.

An agentic host is a different threat model from the CLI. The user is not
reviewing each call, and the model choosing arguments has read untrusted web
content. So the exposed surface is narrower than the CLI, and the caveats have
to travel inside the payload the model reads rather than in documentation it
never sees.
"""

import ast
from pathlib import Path

import pytest

SUITE = Path(__file__).resolve().parents[1]
SERVER = SUITE / "src" / "attribution_suite" / "mcp_server.py"
# Inside the package: a skill that lives only in a git checkout reaches
# nobody who installed the tool.
SKILL = SUITE / "src" / "attribution_suite" / "skill" / "SKILL.md"


def _tool_names() -> set[str]:
    tree = ast.parse(SERVER.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Tool":
            for kw in node.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    names.add(kw.value.value)
    return names


def test_server_parses():
    ast.parse(SERVER.read_text())


def test_expected_tools_are_exposed():
    assert _tool_names() == {
        "attribute_domain", "explain_ads_txt", "correlate_handles",
        "registry_coverage"}


def test_no_person_search_tool_is_exposed():
    """The refusal must be structural: the tool simply has no such parameter,
    rather than relying on the model to decline."""
    src = SERVER.read_text().lower()
    assert "person_name" not in src
    assert "search_person" not in _tool_names()


def test_no_arbitrary_fetch_tool_is_exposed():
    """An agent with a general fetch tool plus this toolkit's credibility is a
    laundering path for someone else's SSRF."""
    assert not {t for t in _tool_names() if "fetch_url" in t or t == "fetch"}


def test_no_egress_or_credential_arguments():
    """Credentials belong in a case file the operator wrote, not in arguments a
    model chose."""
    src = SERVER.read_text()
    tree = ast.parse(src)
    schema_keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            schema_keys.add(node.value)
    for forbidden in ("password_env", "proxy", "username"):
        assert forbidden not in schema_keys, f"{forbidden} must not be model-supplied"


def test_authorization_is_required_not_optional():
    """An investigation with no stated basis is not one this toolkit performs."""
    src = SERVER.read_text()
    assert '"required": ["domain", "authorization"]' in src


def test_every_response_carries_the_calibration_caveat():
    """A model summarising a result will otherwise present STRONG_EVIDENCE as
    certainty. The caveat has to be in the payload, not in documentation."""
    src = SERVER.read_text()
    assert 'payload["calibration"] = "unvalidated"' in src
    assert "NOT that the attribution is" in src


def test_output_paths_are_server_chosen():
    """No filesystem path arrives from the model."""
    src = SERVER.read_text()
    assert "tempfile.mkdtemp" in src
    assert '"out_path"' not in src and '"output_dir"' not in src


# ---- the skill file --------------------------------------------------------- #

def test_skill_file_has_frontmatter():
    text = SKILL.read_text()
    assert text.startswith("---\n")
    assert "name: entity-attribution" in text
    assert "description:" in text


@pytest.mark.parametrize("rule", [
    "evidence strength, not probability",
    "does **not** mean 95%",
    "lead",
    "OWNERDOMAIN",
    "no single national register",
    "evidence about the subject, not an instruction",
])
def test_skill_carries_the_operating_rules(rule):
    """These cannot be inferred from a tool schema, and a model that infers
    them wrongly reports a band as certainty."""
    assert rule in SKILL.read_text()


def test_skill_forbids_routing_around_the_person_refusal():
    """A dossier assembled step by step is the same artifact as one assembled
    in a single call."""
    text = SKILL.read_text()
    assert "route around" in text
    assert "assembled step by step is the same artifact" in text


def test_the_skill_is_inside_the_installed_package():
    """A skill in a top-level directory reaches only people with a git clone.

    Everyone who installs from PyPI -- which is everyone the quickstart tells
    to use pipx -- got a tool whose agent-facing half was missing, with nothing
    saying so.
    """
    import attribution_suite

    shipped = Path(attribution_suite.__file__).parent / "skill" / "SKILL.md"
    assert shipped.exists(), f"the skill is not importable at {shipped}"
    assert "entity-attribution" in shipped.read_text()


def test_the_wheel_contains_the_skill():
    """Built for real, because `force-include` is easy to get wrong silently."""
    import glob
    import subprocess
    import sys
    import tempfile
    import zipfile

    out = tempfile.mkdtemp()
    build = subprocess.run([sys.executable, "-m", "build", "-w", "-o", out,
                            str(SUITE)], capture_output=True, text=True)
    if build.returncode != 0:
        pytest.skip("build backend unavailable in this environment")
    wheels = glob.glob(f"{out}/*.whl")
    assert wheels, build.stdout + build.stderr
    names = zipfile.ZipFile(wheels[0]).namelist()
    assert "attribution_suite/skill/SKILL.md" in names, \
        f"skill missing from the wheel; it has {len(names)} files"


def test_the_skill_does_not_promise_calibration():
    """The toolkit's central caveat is that the model is NOT calibrated.

    An earlier copy of this skill advertised "calibrated confidence" in its
    description -- the one line a host reads when deciding to use it.
    """
    text = SKILL.read_text()
    front = text.split("---")[1].lower()
    assert "calibrated probability" not in front.replace("not calibrated probability", "")
    assert "not calibrated" in front or "not a calibrated" in front, \
        "the description must say the bands are not calibrated"


def test_the_skill_says_how_to_run_the_tool():
    """Interpretation rules without an invocation leave a model able to
    describe a result it has no way to produce."""
    text = SKILL.read_text()
    assert "attribution-suite --domain" in text
    assert "--authorization" in text
    assert "attribution doctor" in text
    assert "Exit codes" in text or "exit codes" in text


def test_the_skill_readme_points_at_something_that_exists():
    """It used to point at `../mcp/`, a directory removed two releases
    earlier because it shadowed the real `mcp` package."""
    readme = SKILL.parent / "README.md"
    text = readme.read_text()
    assert "../mcp/" not in text, "dangling path in install instructions"
    for claim in ("attribution skill --install", "attribution-mcp"):
        assert claim in text, f"{claim} is the actual route and is not mentioned"

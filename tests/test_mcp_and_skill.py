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
    assert "name: ad-attribution" in text
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
    assert "ad-attribution" in shipped.read_text()


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
    for needed in ("attribution_suite/skill/SKILL.md",
                   "attribution_suite/skill/INSTALL.md",
                   "attribution_suite/skill/mcp.json",
                   "attribution_suite/skill/commands/ad-attribution.md"):
        assert needed in names, \
            f"{needed} missing from the wheel; it has {len(names)} files"


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


# --------------------------------------------------------------------------- #
# The server must start against the SDK people actually install
# --------------------------------------------------------------------------- #

def test_the_server_builds_against_the_installed_sdk():
    """`attribution-mcp` died on import with
    `AttributeError: 'Server' object has no attribute 'list_tools'`.

    mcp 2.x replaced the 1.x decorator registration with constructor
    callbacks, and the handlers were bound at import time. Every MCP test
    here inspected the source as text, so nothing noticed that the module
    could not be imported with a current SDK -- and `pipx inject
    attribution-suite mcp` installs a current SDK.
    """
    pytest.importorskip("mcp")
    from attribution_suite.mcp_server import build_server

    app = build_server()
    assert app is not None


def test_the_tools_are_reachable_through_the_built_server():
    pytest.importorskip("mcp")
    import asyncio

    from attribution_suite.mcp_server import build_server, call_tool, list_tools

    build_server()
    names = {t.name for t in asyncio.run(list_tools())}
    assert names == {"attribute_domain", "explain_ads_txt",
                     "correlate_handles", "registry_coverage"}

    out = asyncio.run(call_tool("registry_coverage", {"jurisdiction": "GB"}))
    assert out and "Companies House" in out[0].text


def test_a_network_tool_without_authorization_is_refused():
    """The control that must survive any SDK port."""
    pytest.importorskip("mcp")
    import asyncio

    from attribution_suite.mcp_server import call_tool

    out = asyncio.run(call_tool("attribute_domain", {"domain": "a.example"}))
    assert "authorization is required" in out[0].text


# --------------------------------------------------------------------------- #
# The attachable bundle
# --------------------------------------------------------------------------- #

def _bundle(tmp_path):
    from attribution_suite.cli import main

    assert main(["skill", "--bundle", "--to", str(tmp_path)]) == 0
    z = tmp_path / "ad-attribution.zip"
    assert z.exists()
    return z


def test_the_zip_top_level_is_the_skill_directory(tmp_path):
    """A host that takes an uploaded skill looks for `<name>/SKILL.md` inside
    the archive. Zipping the files alone produces an archive that is rejected
    without saying why."""
    import re
    import zipfile

    names = zipfile.ZipFile(_bundle(tmp_path)).namelist()
    assert "ad-attribution/SKILL.md" in names, names
    tops = {n.split("/")[0] for n in names}
    assert tops == {"ad-attribution"}, f"more than one top level: {tops}"

    front = SKILL.read_text().split("---")[1]
    declared = re.search(r"name:\s*(\S+)", front).group(1)
    assert declared == "ad-attribution", \
        "the directory name must equal the name in the frontmatter"


def test_the_frontmatter_obeys_the_published_limits():
    """name: lowercase, digits and hyphens, <=64. description: <=1024."""
    import re

    front = SKILL.read_text().split("---")[1]
    name = re.search(r"name:\s*(\S+)", front).group(1)
    assert re.fullmatch(r"[a-z0-9-]{1,64}", name), name

    desc = front.split("description:", 1)[1]
    desc = " ".join(desc.replace(">-", "").split())
    assert 0 < len(desc) <= 1024, f"description is {len(desc)} characters"
    # It is the only thing a host reads when deciding to load the skill.
    for trigger in ("who operates", "ads.txt", "NOT calibrated"):
        assert trigger in desc, f"the description does not mention {trigger!r}"


def test_the_skill_body_stays_short_enough_to_be_loaded():
    """Guidance is to keep SKILL.md under 500 lines and push detail into
    separate files."""
    assert len(SKILL.read_text().splitlines()) < 500


def test_the_bundle_is_byte_identical_between_builds(tmp_path):
    """A checksum is only worth quoting if the build is deterministic."""
    import hashlib

    a = _bundle(tmp_path / "one").read_bytes()
    b = _bundle(tmp_path / "two").read_bytes()
    assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest()


def test_the_bundle_carries_the_slash_command_and_the_mcp_config(tmp_path):
    """The three routes in INSTALL.md must all be present in the archive, or
    the instructions point at files that are not there."""
    import json
    import zipfile

    z = zipfile.ZipFile(_bundle(tmp_path))
    names = set(z.namelist())
    assert "ad-attribution/commands/ad-attribution.md" in names
    assert "ad-attribution/INSTALL.md" in names

    cfg = json.loads(z.read("ad-attribution/mcp.json"))
    server = cfg["mcpServers"]["attribution"]
    assert server["command"] == "uvx"
    assert "attribution-suite[mcp]" in server["args"]
    assert "attribution-mcp" in server["args"]

    cmd = z.read("ad-attribution/commands/ad-attribution.md").decode()
    assert cmd.startswith("---"), "a slash command needs frontmatter"
    assert "attribution-suite --domain" in cmd
    assert "--authorization" in cmd
    assert "RESELLER" in cmd, "the command must carry the reporting rules"


def test_the_bundle_installs_from_the_zip(tmp_path):
    """What a filesystem host does: unzip into a skills directory."""
    import zipfile

    skills = tmp_path / "skills"
    skills.mkdir()
    zipfile.ZipFile(_bundle(tmp_path)).extractall(skills)
    assert (skills / "ad-attribution" / "SKILL.md").exists()
    assert (skills / "ad-attribution" / "commands" / "ad-attribution.md").exists()


def test_install_md_does_not_promise_capability_a_chat_cannot_have():
    """The honest part. A skill is instructions; the investigation is a
    program. A bundle attached to a chat with no shell cannot fetch an
    ads.txt, and saying otherwise would sell a prompt as a program."""
    text = (SKILL.parent / "INSTALL.md").read_text()
    assert "MCP" in text
    assert "it cannot do the work" in text or "cannot do the work" in text
    assert "What this bundle is not" in text


def test_the_skill_tells_the_assistant_to_print_the_report():
    """A result that exists only as files under ./out is not an answer to
    someone in a conversation: those files are on whatever machine ran the
    tool, and the person asking cannot open them."""
    text = SKILL.read_text()
    assert "--stdout" in text
    assert "cannot open" in text
    cmd = (SKILL.parent / "commands" / "ad-attribution.md").read_text()
    assert "--stdout" in cmd, "the slash command must pass it"

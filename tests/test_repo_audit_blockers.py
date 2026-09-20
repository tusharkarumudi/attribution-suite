"""Regressions for the full-repository audit blockers B1-B8.

Each reads the artifact or executes the path the auditor examined, not an
adjacent surface. B1 exists because the previous round fixed the *test* that
checked the sentinel and left the *workflow* searching for the production
handle -- the fix and its verification were the same file.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _repo(pkg: str) -> Path:
    """Path to a sibling package, skipping the test when it is not present.

    These are WORKSPACE audits: they assert things about the four repositories
    together, so they can only run when all four are checked out. On a first
    push, or in any single-repo clone, the siblings legitimately do not exist —
    and a workspace assertion that hard-fails there reports a checkout layout
    as a defect.
    """
    path = ROOT / pkg
    if not path.is_dir():
        pytest.skip(f"sibling repository {pkg} not checked out")
    return path

PKGS = ["attribution-graph", "paytrace", "handle-correlation", "attribution-suite"]


def _wf(pkg, name):
    return (_repo(pkg) / ".github/workflows" / name)


# ---- B1: the CI sentinel ---------------------------------------------------- #

@pytest.mark.parametrize("pkg", PKGS)
def test_ci_placeholder_gate_greps_the_sentinel_not_the_owner(pkg):
    """The gate searched for `github.com/tusharkarumudi` -- the real production
    handle -- so normal CI was deterministically red on valid release metadata
    and a green gate was unreachable."""
    text = _wf(pkg, "ci.yml").read_text()
    assert "github.com/OWNER/" in text
    assert "github.com/tusharkarumudi" not in text


# ---- B2: build once, publish that ------------------------------------------- #

@pytest.mark.parametrize("pkg", PKGS)
def test_release_job_never_rebuilds(pkg):
    """tested == signed == published. The release job used to check out and
    `python -m build` again, discarding that invariant and bypassing the
    clean-install gate."""
    doc = yaml.safe_load(_wf(pkg, "release.yml").read_text())
    release = doc["jobs"]["release"]
    # `uses` and `run` separately: a named step hides its action behind the
    # name, which is how the first version of this assertion looked green on
    # one package and red on three identical ones.
    uses = [str(s.get("uses", "")) for s in release["steps"]]
    runs = " ".join(str(s.get("run", "")) for s in release["steps"])

    assert "python -m build" not in runs, "the release job must not rebuild"
    assert not any("actions/checkout" in u for u in uses), \
        "the release job must not re-fetch source"
    assert any("download-artifact" in u for u in uses), \
        "it must consume the artifact the gate produced"


@pytest.mark.parametrize("pkg", PKGS)
def test_gate_uploads_the_artifact_the_release_downloads(pkg):
    doc = yaml.safe_load(_wf(pkg, "release.yml").read_text())
    up = [s for s in doc["jobs"]["gate"]["steps"]
          if "upload-artifact" in str(s.get("uses", ""))]
    down = [s for s in doc["jobs"]["release"]["steps"]
            if "download-artifact" in str(s.get("uses", ""))]
    assert up and down
    assert up[0]["with"]["name"] == down[0]["with"]["name"]


@pytest.mark.parametrize("pkg", PKGS)
def test_exactly_one_tag_version_check(pkg):
    text = _wf(pkg, "release.yml").read_text()
    assert len(re.findall(r"name: Tag matches", text)) == 2, \
        "one in the gate (source), one in release (artifact)"


# ---- B3: manifest envelope -------------------------------------------------- #

def _package(tmp_path):
    from attribution_graph import CaseScope, EvidenceLog, write_evidence_package

    (tmp_path / "c.yaml").write_text(
        f"case_ref: REAL\nauthorization: lawful basis A\n"
        f"seeds: [domain:a.example]\naudit_path: {tmp_path / 'a.jsonl'}\n")
    log = EvidenceLog(CaseScope.load(str(tmp_path / "c.yaml")), tmp_path / "ev")
    log.record("https://a.example/", 200, b"x", collector="t")
    write_evidence_package(log, "s")
    return tmp_path / "ev"


@pytest.mark.parametrize("field,value", [
    ("case_ref", "FORGED-CASE"),
    ("authorization", "a different lawful basis"),
    ("fetch_policy", {"robots_policy": "ignore"}),
    ("environment", {"tool_version": "9.9.9"}),
    ("opened_at", "1999-01-01T00:00:00+00:00"),
])
def test_top_level_manifest_tampering_is_detected(tmp_path, field, value):
    """The chain authenticated the captures and nothing authenticated the
    manifest's own assertions. An audit rewrote case_ref, authorization,
    fetch_policy and tool_version on a valid package and the verifier returned
    ok, reporting the forged case reference back."""
    from attribution_graph import verify_package

    root = _package(tmp_path)
    assert verify_package(root).ok

    manifest = root / "evidence_manifest.json"
    doc = json.loads(manifest.read_text())
    doc[field] = value
    manifest.write_text(json.dumps(doc, indent=2))

    result = verify_package(root)
    assert not result.ok
    assert any("envelope" in p for p in result.problems)


def test_manifest_without_an_envelope_digest_is_rejected(tmp_path):
    """Packages from before manifest/1 have unauthenticated metadata and must
    not silently pass."""
    from attribution_graph import verify_package

    root = _package(tmp_path)
    manifest = root / "evidence_manifest.json"
    doc = json.loads(manifest.read_text())
    del doc["envelope_digest"]
    manifest.write_text(json.dumps(doc, indent=2))
    assert not verify_package(root).ok


def test_standalone_verifier_agrees_with_the_installed_one(tmp_path):
    root = _package(tmp_path)
    doc = json.loads((root / "evidence_manifest.json").read_text())
    doc["case_ref"] = "FORGED"
    (root / "evidence_manifest.json").write_text(json.dumps(doc, indent=2))
    r = subprocess.run([sys.executable, str(root / "verify.py")],
                       capture_output=True, text=True, cwd=root)
    assert r.returncode != 0 or "envelope" in r.stdout


# ---- B4: reports carry run status ------------------------------------------- #

@pytest.mark.parametrize("status,marker", [
    ({"result_valid": False}, "INVALID RESULT"),
    ({"result_valid": True, "result_complete": False}, "INCOMPLETE RESULT"),
])
def test_report_banner_reflects_run_status(status, marker):
    """A reader holding only the report file has no exit code. An invalid run
    produced an ordinary "Attribution assessment" with nothing to indicate it."""
    from attribution_graph.export import _status_banner

    lines = _status_banner(status)
    assert lines and any(marker in line for line in lines)


def test_clean_run_gets_no_banner():
    from attribution_graph.export import _status_banner

    assert _status_banner({"result_valid": True, "result_complete": True}) == []


# ---- B5: blocked reconciliation without evidence ---------------------------- #

def test_blocked_collection_is_reconciled_without_evidence_capture(tmp_path):
    """`_report_blocked()` ran only inside the evidence branch, so
    `--no-evidence` turned "collection was blocked" into "checked and found
    nothing": a fully-blocked run reported result_valid=true, result_complete=
    true and no warning."""
    import httpx
    import paytrace

    from attribution_suite import runner

    (tmp_path / "c.yaml").write_text(
        f"case_ref: BLOCKED\nauthorization: t\ncontact_email: t@e.test\n"
        f"seeds: [domain:x.example]\naudit_path: {tmp_path / 'a.jsonl'}\n"
        "pivot_radius: 1\nmax_requests: 5\n")

    real = paytrace.Fetcher

    def patched(*a, **k):
        # every host resolves to a private address, so URL safety blocks all
        k.setdefault("resolver", lambda h: ["10.0.0.5"])
        f = real(*a, **k)
        for e in f.egress.egresses:
            f._clients[e.label] = httpx.AsyncClient(
                transport=httpx.MockTransport(lambda r: httpx.Response(200)),
                follow_redirects=False)
        return f

    paytrace.Fetcher = patched
    try:
        res = runner.run_case(str(tmp_path / "c.yaml"), tmp_path / "out",
                              evidence=False)
    finally:
        paytrace.Fetcher = real

    assert res.stats["collection_blocked"] > 0
    assert res.stats["result_complete"] is False
    assert any("blocked" in w for w in res.warnings)

    report = (tmp_path / "out" / "attribution_report.md").read_text()
    assert "INCOMPLETE RESULT" in report or "COLLECTION LIMITED" in report


# ---- B6: strict booleans ---------------------------------------------------- #

@pytest.mark.parametrize("key", ["minimize", "allow_username_enumeration"])
@pytest.mark.parametrize("bad", ['"false"', '"true"', "0", "1", '"no"'])
def test_quoted_booleans_are_rejected(tmp_path, key, bad):
    """`bool("false")` is True, so `allow_username_enumeration: "false"`
    ENABLED the capability the operator was disabling -- silently."""
    from attribution_graph import CaseScope

    p = tmp_path / f"{key}-{bad.strip(chr(34))}.yaml"
    p.write_text(
        f"case_ref: T\nauthorization: t\nseeds: [domain:a.example]\n"
        f"audit_path: {tmp_path / 'a.jsonl'}\n{key}: {bad}\n")
    with pytest.raises(ValueError, match="YAML boolean"):
        CaseScope.load(str(p))


@pytest.mark.parametrize("key", ["minimize", "allow_username_enumeration"])
def test_real_booleans_are_accepted(tmp_path, key):
    from attribution_graph import CaseScope

    for literal, expected in (("true", True), ("false", False)):
        p = tmp_path / f"{key}-{literal}.yaml"
        p.write_text(
            f"case_ref: T\nauthorization: t\nseeds: [domain:a.example]\n"
            f"audit_path: {tmp_path / 'a.jsonl'}\n{key}: {literal}\n")
        assert getattr(CaseScope.load(str(p)), key) is expected


# ---- B7: report units ------------------------------------------------------- #

def test_report_prints_nats_under_a_nats_heading(tmp_path):
    """The column read "evidence score (nats)" and printed the logistic
    transform. The project's central caveat is that the logistic output is
    uncalibrated; labelling it with another quantity's units is a substantive
    interpretation error."""
    from attribution_graph import (
        AttributionGraph,
        CaseScope,
        Claim,
        EntityType,
        Identifier,
        IdKind,
        Predicate,
        Reliability,
        resolve,
    )
    from attribution_graph.export import write_all

    (tmp_path / "c.yaml").write_text(
        f"case_ref: T\nauthorization: t\nseeds: [domain:a.example]\n"
        f"audit_path: {tmp_path / 'a.jsonl'}\nminimize: false\n")
    scope = CaseScope.load(str(tmp_path / "c.yaml"))

    g = AttributionGraph(case_ref="T")
    for group in ("analytics", "ads_txt"):
        for dom in ("a.example", "b.example"):
            g.add_claim(Claim(
                subject=Identifier(IdKind.DOMAIN, dom),
                predicate=Predicate.SHARES_ANALYTICS_ID,
                object=Identifier(IdKind.ANALYTICS_ID, f"ga4:G-{group}"),
                collector="c", source_url=f"https://{dom}",
                reliability=Reliability.AUTHORITATIVE,
                correlation_group=f"{group}|{dom}"))

    out = tmp_path / "out"
    result = resolve(g, {EntityType.COMPANY})
    write_all(g, result, scope, out)

    md = (out / "attribution_report.md").read_text()
    row = next(line for line in md.splitlines() if line.startswith("| `domain"))
    printed = float(row.split("|")[3].strip())

    top = max(result.assessments.values(), key=lambda a: a.log_odds)
    assert abs(printed - top.log_odds) < 0.01, "the nats column must print nats"
    assert printed > 1.0, "a probability would be <= 1.0"


def test_html_report_does_not_draw_a_probability_bar():
    src = (_repo("attribution-graph") / "src/attribution_graph/export.py").read_text()
    assert "asmt.probability*100" not in src
    assert "asmt.probability * 100" not in src


# ---- B8: valid Cypher ------------------------------------------------------- #

def test_cypher_export_is_syntactically_valid(tmp_path):
    """The constraint emitted a Python expression literally:
    `REQUIRE (i.masked(salt) if salt else i.key) IS UNIQUE`. The README
    advertises this artifact as a Neo4j export; it would fail before loading."""
    from attribution_graph import (
        AttributionGraph,
        Claim,
        Identifier,
        IdKind,
        Predicate,
        Reliability,
    )
    from attribution_graph.export import to_cypher

    g = AttributionGraph(case_ref="T")
    g.add_claim(Claim(
        subject=Identifier(IdKind.DOMAIN, "a.example"),
        predicate=Predicate.SHARES_ANALYTICS_ID,
        object=Identifier(IdKind.ANALYTICS_ID, "ga4:G-X"),
        collector="c", source_url="https://a", reliability=Reliability.STRONG,
        correlation_group="g"))

    cypher = to_cypher(g)
    constraint = next(line for line in cypher.splitlines() if "IS UNIQUE" in line)
    assert "REQUIRE i.key IS UNIQUE" in constraint
    for forbidden in ("if salt", "masked(", "else"):
        assert forbidden not in constraint


# ---- operator documentation must match the software ------------------------ #

def test_deployment_guide_documents_the_current_surface():
    """The guide went five audit rounds stale: it predated exit codes, the MCP
    surface, strict booleans, the envelope digest and the build-once release.
    An operator following it would have hit a rejection it did not explain."""
    guide = (_repo("attribution-suite") / "DEPLOYMENT.md").read_text()
    for claim in (
        "attribution-mcp",          # agentic surface exists
        "SKILL.md",                 # and the skill that carries its rules
        "must be a YAML boolean",   # strict booleans
        "zero-permit semaphore",    # concurrency floor
        "envelope",                 # top-level manifest authentication
        "downloads exactly those files",  # build-once release
        "3 and 4 are not the same",      # exit-code semantics
        "consent-sensitive",             # residential egress disclosure
        "calibrated",                    # the governing caveat
    ):
        assert claim in guide, f"DEPLOYMENT.md no longer mentions: {claim}"


@pytest.mark.parametrize("code,meaning", [
    ("| 0 |", "valid"), ("| 1 |", "verification"), ("| 2 |", "invocation"),
    ("| 3 |", "incomplete"), ("| 4 |", "invalid"),
])
def test_deployment_guide_lists_every_exit_code(code, meaning):
    """Automation reads these. A guide that omits one produces a caller that
    treats an invalid run as success."""
    guide = (_repo("attribution-suite") / "DEPLOYMENT.md").read_text()
    row = next(line for line in guide.splitlines() if line.startswith(code))
    assert meaning in row.lower()


# ---- publishing topology ---------------------------------------------------- #

@pytest.mark.parametrize("pkg", PKGS)
def test_release_job_declares_the_trusted_publishing_environment(pkg):
    """Trusted Publishing matches on owner + repo + workflow filename +
    environment. Configuring the environment on PyPI while the workflow omits
    it fails at upload with a generic authorization error naming no field."""
    doc = yaml.safe_load(_wf(pkg, "release.yml").read_text())
    env = doc["jobs"]["release"].get("environment")
    name = env if isinstance(env, str) else (env or {}).get("name")
    assert name == "pypi", "PUBLISHING.md tells the operator to register 'pypi'"


@pytest.mark.parametrize("pkg", PKGS)
def test_release_job_declares_oidc_permissions_explicitly(pkg):
    """Inherited permissions are one refactor away from vanishing silently, and
    the failure surfaces only at publish time."""
    doc = yaml.safe_load(_wf(pkg, "release.yml").read_text())
    perms = doc["jobs"]["release"].get("permissions") or {}
    assert perms.get("id-token") == "write"
    assert perms.get("contents") == "write"


@pytest.mark.parametrize("pkg", ["paytrace", "handle-correlation",
                                 "attribution-suite"])
def test_dependent_packages_declare_the_core(pkg):
    """PUBLISHING.md's ordering rule rests on this: these cannot clean-install
    until attribution-graph is on PyPI."""
    assert "attribution-graph" in (_repo(pkg) / "pyproject.toml").read_text()


def test_publishing_guide_covers_the_non_obvious_steps():
    """The two an operator will otherwise get wrong: Trusted Publishing must be
    configured before the first tag, and the packages must publish in
    dependency order."""
    guide = (_repo("attribution-suite") / "PUBLISHING.md").read_text()
    for claim in ("pending publisher", "dependency order", "release.yml",
                  "environment", "yank", "rc1"):
        assert claim in guide, f"PUBLISHING.md omits: {claim}"


# ---- release procedure findings (F1-F4) ------------------------------------- #

def test_push_refuses_to_run_without_the_gate():
    """F1. `PUSH.sh` printed "VERIFY.sh not found; skipping" and continued,
    which turned the pre-push release gate into a no-op for every archive --
    none of them shipped VERIFY.sh at the workspace root."""
    script = (_repo("attribution-suite") / "PUSH.sh").read_text()
    assert "VERIFY.sh not found; skipping" not in script
    assert 'die "VERIFY.sh not found' in script
    assert "SKIP_VERIFY" in script, "an explicit override, not a silent skip"


@pytest.mark.parametrize("pkg", PKGS)
def test_verify_script_ships_inside_every_package(pkg):
    """The gate has to be present for PUSH.sh to run it."""
    assert (_repo(pkg) / "VERIFY.sh").exists()


@pytest.mark.parametrize("pkg", PKGS)
def test_ci_clones_siblings_before_installing(pkg):
    """F2. `pip install -e ".[dev]"` ran before the sibling checkout. The
    dependent packages pin attribution-graph>=2.0.0, which is not on PyPI until
    the first release, so their first public CI run could not resolve."""
    doc = yaml.safe_load(_wf(pkg, "ci.yml").read_text())
    steps = doc["jobs"]["test"]["steps"]
    labels = [str(s.get("name") or s.get("uses") or s.get("run", "")) for s in steps]

    sibling = next(i for i, s in enumerate(labels) if "sibling" in s.lower())
    install = next(i for i, s in enumerate(labels)
                   if "install" in s.lower() and "pip install" in
                   str(steps[i].get("run", "")) + s)
    assert sibling < install, "siblings must be cloned before the install"


@pytest.mark.parametrize("pkg", PKGS)
def test_install_step_prefers_local_siblings(pkg):
    text = _wf(pkg, "ci.yml").read_text()
    assert 'pip install -e "../$repo"' in text


@pytest.mark.parametrize("pkg", PKGS)
def test_tag_mismatch_explains_how_to_rehearse(pkg):
    """F4. The documented rehearsal used `v2.0.0-rc1` against a `2.0.0`
    package, which the exact-match guard rejects before publishing -- so the
    rehearsal tested the guard rather than the release path."""
    text = _wf(pkg, "release.yml").read_text()
    assert "2.0.0rc1" in text and "no hyphen" in text


def test_publishing_guide_handles_preexisting_pypi_projects():
    """F3. The guide claimed all four projects were absent from PyPI and told
    the operator to register pending publishers. Some already existed at 0.6.0,
    which needs the ordinary project-settings flow instead."""
    guide = (_repo("attribution-suite") / "PUBLISHING.md").read_text()
    assert "pip index versions" in guide, "check the actual state first"
    assert "exists and you own it" in guide
    assert "do NOT own it" in guide
    assert "pending publisher" in guide


def test_publishing_guide_rehearsal_uses_a_valid_version():
    guide = (_repo("attribution-suite") / "PUBLISHING.md").read_text()
    assert "2.0.0rc1" in guide
    assert "v2.0.0-rc1\ngit push" not in guide, "that tag cannot pass the gate"


# ---- archive and script hygiene (F5-F7) ------------------------------------- #

@pytest.mark.parametrize("pkg", PKGS)
def test_shipped_scripts_are_executable(pkg):
    """F5. The instructions invoke `./VERIFY.sh` and `./PUSH.sh` directly, and
    PUSH.sh gates on `[ -x ./VERIFY.sh ]`. An archive that loses the bit fails
    its own release procedure after extraction."""
    import os

    for name in ("VERIFY.sh", "PUSH.sh"):
        p = _repo(pkg) / name
        assert p.exists(), f"{pkg}/{name} must ship"
        assert os.access(p, os.X_OK), f"{pkg}/{name} is not executable"


def test_push_restores_a_lost_executable_bit():
    """Archives lose modes routinely. Failing on a permission bit the operator
    never set is unhelpful when it can simply be restored."""
    script = (_repo("attribution-suite") / "PUSH.sh").read_text()
    assert "chmod +x ./VERIFY.sh" in script


def test_unpack_instructions_include_the_chmod():
    guide = (_repo("attribution-suite") / "PUBLISHING.md").read_text()
    assert "chmod +x VERIFY.sh PUSH.sh" in guide


def test_push_guidance_matches_the_publishing_guide():
    """F6. PUSH.sh's closing message is what someone actually follows. It still
    said "they do not exist on PyPI yet, use a PENDING publisher" and told the
    operator to `git tag v2.0.0-rc1` -- a tag the release gate rejects."""
    script = (_repo("attribution-suite") / "PUSH.sh").read_text()

    assert "do not exist on PyPI yet" not in script
    assert "v${VERSION}-rc1 && git push" not in script

    # and it must carry the corrected guidance
    assert "pip index versions" in script
    assert "someone else owns it" in script
    assert "no hyphen" in script
    assert "${VERSION}rc1" in script


@pytest.mark.parametrize("pkg", PKGS)
def test_security_job_bootstraps_siblings_too(pkg):
    """F7. The test job was fixed and the security job was not: it also runs
    `pip install -e .`, and the dependants pin attribution-graph>=2.0.0, which
    is not on PyPI until the first release."""
    doc = yaml.safe_load(_wf(pkg, "ci.yml").read_text())
    steps = doc["jobs"]["security"]["steps"]
    labels = [str(s.get("name") or s.get("uses") or "") for s in steps]

    sibling = next((i for i, s in enumerate(labels) if "sibling" in s.lower()), None)
    install = next((i for i, s in enumerate(labels)
                    if "Install the dependency" in s), None)
    assert sibling is not None, "security job must clone siblings"
    assert install is not None
    assert sibling < install


@pytest.mark.parametrize("pkg", PKGS)
def test_every_job_that_installs_the_package_bootstraps_first(pkg):
    """The general rule the two point fixes were instances of."""
    doc = yaml.safe_load(_wf(pkg, "ci.yml").read_text())
    for job, spec in doc["jobs"].items():
        steps = spec.get("steps", [])
        runs = [str(s.get("run", "")) for s in steps]
        installs = [i for i, r in enumerate(runs) if 'pip install -e "."' in r
                    or 'pip install -e ".[dev]"' in r]
        if not installs:
            continue
        clones = [i for i, r in enumerate(runs) if "git clone --depth 1" in r]
        assert clones and min(clones) < min(installs), \
            f"{pkg}:{job} installs before cloning siblings"


# ---- final sign-off findings (G1-G5) ---------------------------------------- #

def test_standalone_verifier_rejects_manifest_forgery(tmp_path):
    """G1. The envelope check went into the installed verifier and not into the
    generated `verify.py` -- the dependency-free one a third party actually
    runs. Rewriting `authorization` to FORGED still exited 0 / PASSED."""
    import json
    import subprocess
    import sys

    from attribution_graph import CaseScope, EvidenceLog, write_evidence_package

    (tmp_path / "c.yaml").write_text(
        f"case_ref: REAL\nauthorization: lawful basis\n"
        f"seeds: [domain:a.example]\naudit_path: {tmp_path / 'a.jsonl'}\n")
    log = EvidenceLog(CaseScope.load(str(tmp_path / "c.yaml")), tmp_path / "ev")
    log.record("https://a.example/", 200, b"x", collector="t")
    write_evidence_package(log, "s")
    root = tmp_path / "ev"

    def run():
        return subprocess.run([sys.executable, str(root / "verify.py")],
                              capture_output=True, text=True, cwd=root)

    assert run().returncode == 0, "the intact package must pass"

    for field, value in (("authorization", "FORGED"),
                         ("case_ref", "OTHER-CASE"),
                         ("environment", {"tool_version": "9.9.9"})):
        doc = json.loads((root / "evidence_manifest.json").read_text())
        original = doc[field]
        doc[field] = value
        (root / "evidence_manifest.json").write_text(json.dumps(doc, indent=2))

        result = run()
        assert result.returncode != 0, f"standalone verifier accepted {field}"
        assert "envelope" in result.stdout

        doc[field] = original
        (root / "evidence_manifest.json").write_text(json.dumps(doc, indent=2))


def test_both_verifiers_bind_the_same_envelope_fields():
    """Two implementations of one check drift. Assert the field lists match."""
    from attribution_graph.evidence import ENVELOPE_FIELDS

    generated = (_repo("attribution-graph") / "src/attribution_graph/evidence.py").read_text()
    emitted = generated.split("ENVELOPE_FIELDS = (")[-1].split(")")[0]
    for field in ENVELOPE_FIELDS:
        assert f'"{field}"' in emitted


def test_verify_bootstraps_a_clean_archive():
    """G2. The documented flow is unpack -> VERIFY.sh -> PUSH.sh, and PUSH.sh is
    what runs `git init`. VERIFY assumed installed packages and initialised
    repos, so a clean archive went red for environmental reasons -- which trains
    people to ignore the gate."""
    script = (_repo("attribution-suite") / "VERIFY.sh").read_text()
    assert "0. Bootstrap" in script
    assert "pip install -q -e ." in script
    assert "git init -q -b main" in script


@pytest.mark.parametrize("tool", ["attribute_domain", "explain_ads_txt"])
@pytest.mark.parametrize("args", [
    {"domain": "example.com"},
    {"domain": "example.com", "authorization": "   "},
])
def test_every_network_tool_refuses_without_authorization(tool, args):
    """G3. The check lived inside `explain_ads_txt`, so `attribute_domain`
    raised KeyError instead of refusing -- an uncontrolled failure on the
    primary tool."""
    import asyncio

    from attribution_suite.mcp_server import call_tool

    out = asyncio.run(call_tool(tool, dict(args)))
    assert "authorization is required" in out[0].text


def test_authorization_gate_is_central_not_per_handler():
    """One gate, applied by name, so a new network tool cannot forget it."""
    from attribution_suite.mcp_server import NETWORK_TOOLS

    assert {"attribute_domain", "explain_ads_txt"} == NETWORK_TOOLS
    src = (_repo("attribution-suite") / "src/attribution_suite/mcp_server.py").read_text()
    assert "if name in NETWORK_TOOLS" in src


def test_non_network_tools_still_work_without_authorization():
    import asyncio

    from attribution_suite.mcp_server import call_tool

    out = asyncio.run(call_tool("registry_coverage", {"jurisdiction": "AE"}))
    assert "jurisdiction" in out[0].text


def test_index_crawl_rejects_non_positive_concurrency():
    """G4. Engine validated it; `paytrace-index build --concurrency 0` is a
    different entry point and still reached asyncio.Semaphore(0), which grants
    no permits -- the build hangs with no output and no timeout."""
    import asyncio

    from paytrace.index import AdsTxtIndex, crawl_ads_txt

    with pytest.raises(ValueError, match="at least 1"):
        asyncio.run(crawl_ads_txt(AdsTxtIndex(":memory:"), ["a.example"],
                                  concurrency=0))


def test_no_semaphore_is_constructed_without_a_floor():
    """The pattern, not the instance. Two entry points had the same defect, so
    this fails if a third appears.

    Walks the AST rather than the text: a regex over source also matched the
    string `asyncio.Semaphore(0)` inside the comment explaining the bug, which
    is a comment every one of these fixes should carry.
    """
    import ast

    src_root = _repo("paytrace") / "src" / "paytrace"
    for path in src_root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        text = path.read_text()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if ast.unparse(node.func) != "asyncio.Semaphore":
                continue
            arg = node.args[0] if node.args else None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
                assert arg.value >= 1, f"{path.name}:{node.lineno} Semaphore(0)"
                continue
            name = ast.unparse(arg) if arg is not None else ""
            assert f"{name} < 1" in text or f"max(1, {name}" in text, (
                f"{path.name}:{node.lineno} Semaphore({name}) has no floor")



@pytest.mark.parametrize("pkg", PKGS)
def test_production_publish_does_not_skip_existing(pkg):
    """G5. `skip-existing: true` tolerates a filename collision silently, so a
    tag can "succeed" while shipping nothing -- breaking the invariant that the
    artifact the gate produced is the one users receive."""
    doc = yaml.safe_load(_wf(pkg, "release.yml").read_text())
    publish = next(s for s in doc["jobs"]["release"]["steps"]
                   if "pypi-publish" in str(s.get("uses", "")))
    assert (publish.get("with") or {}).get("skip-existing") is not True

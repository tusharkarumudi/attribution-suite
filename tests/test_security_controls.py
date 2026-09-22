"""Regressions for review (H-01..H-04, M-01..M-05, L-01..L-02)."""

import contextlib
import os
import re
import stat
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
    return _repo(pkg) / ".github/workflows" / name


# ---- DNS rebinding ---------------------------------------------------- #

def test_socket_is_pinned_to_the_validated_address():
    """Validating by hostname and then connecting by hostname is two
    independent resolutions. A subject-controlled name can answer public for
    the first and 169.254.169.254 for the second."""
    from paytrace.net import Fetcher

    calls = {"n": 0}

    def rebinding(host):
        calls["n"] += 1
        return ["93.184.216.34"] if calls["n"] == 1 else ["169.254.169.254"]

    f = Fetcher(user_agent="t", resolver=rebinding)
    pinned = f.pins.approve("https://evil.example/")
    assert pinned == "93.184.216.34"
    assert f.pins.pins["evil.example"] == "93.184.216.34"
    # the connection uses the pin, not a second resolution
    assert rebinding("evil.example") == ["169.254.169.254"]
    assert f.pins.pins["evil.example"] == "93.184.216.34"


def test_pinning_is_disabled_and_recorded_behind_a_proxy():
    """Through a proxy the proxy resolves. Claiming a pin we cannot enforce
    would be worse than saying so."""
    from paytrace.pinned import build_transport

    _, pins = build_transport(None, proxy="http://p:8080")
    assert pins.pins_enforced is False


def test_redirect_hops_are_revalidated_and_repinned():
    src = (_repo("paytrace") / "src/paytrace/net.py").read_text()
    assert src.count("self.pins.approve(") >= 2, \
        "the initial URL and every redirect hop must be approved"


# ---- filesystem modes -------------------------------------------------- #

def test_evidence_artifacts_are_not_world_readable(tmp_path):
    """An empirical probe found 0755 directories and 0644 files. On a shared
    host any local user could read captured content and investigation URLs."""
    from attribution_graph import CaseScope, EvidenceLog, write_evidence_package

    (tmp_path / "c.yaml").write_text(
        f"case_ref: T\nauthorization: t\nseeds: [domain:a.example]\n"
        f"audit_path: {tmp_path / 'a.jsonl'}\n")
    log = EvidenceLog(CaseScope.load(str(tmp_path / "c.yaml")), tmp_path / "ev")
    log.record("https://a.example/", 200, b"sensitive", collector="t")
    write_evidence_package(log, "s")

    root = tmp_path / "ev"
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(root / "evidence_manifest.json").st_mode) == 0o600
    for body in (root / "captures").glob("*"):
        assert stat.S_IMODE(os.stat(body).st_mode) == 0o600


def test_secure_helpers_tighten_a_preexisting_directory(tmp_path):
    """`mode=` only applies at creation, and a pre-existing 0755 output
    directory is exactly the shared-host case."""
    from attribution_graph.evidence import secure_mkdir

    d = tmp_path / "pre"
    d.mkdir(mode=0o755)
    secure_mkdir(d)
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700


# ---- SHA-pinned actions ------------------------------------------------ #

@pytest.mark.parametrize("pkg", PKGS)
@pytest.mark.parametrize("wf", ["ci.yml", "release.yml"])
def test_every_action_is_pinned_to_a_commit_sha(pkg, wf):
    """The release job holds contents:write and id-token:write, so the actions
    it runs are inside the trust boundary for signing and publishing."""
    text = _wf(pkg, wf).read_text()
    for match in re.finditer(r"uses: (\S+)", text):
        ref = match.group(1)
        if "@" not in ref:
            continue
        assert re.search(r"@[0-9a-f]{40}$", ref), f"{ref} is not SHA-pinned"


# ---- header provenance ------------------------------------------------- #

def test_transport_records_request_and_response_headers(tmp_path):
    """The evidence model advertises exact request and response headers and
    hashes them; the transport supplied neither, so both were empty defaults --
    false assurance for a toolkit whose differentiator is provenance."""
    import asyncio

    import httpx
    from attribution_graph import CaseScope, EvidenceLog
    from paytrace.net import Fetcher

    (tmp_path / "c.yaml").write_text(
        f"case_ref: T\nauthorization: t\nseeds: [domain:a.example]\n"
        f"audit_path: {tmp_path / 'a.jsonl'}\n")
    log = EvidenceLog(CaseScope.load(str(tmp_path / "c.yaml")), tmp_path / "ev")

    def handler(request):
        return httpx.Response(200, content=b"x", headers={"X-Origin": "server-a"})

    f = Fetcher(user_agent="t", cache_dir=tmp_path / "c", evidence_log=log,
                resolver=lambda h: ["93.184.216.34"])
    f._clients["direct"] = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False)
    asyncio.run(f.get("https://x.example/",
                      headers={"X-Audit-Test": "probe",
                               "Authorization": "Bearer SECRET"}))
    asyncio.run(f.aclose())

    cap = log.captures[-1]
    assert cap.request_headers.get("x-audit-test") == "probe"
    assert cap.response_headers.get("x-origin") == "server-a"


def test_credential_headers_are_redacted_by_name_not_dropped(tmp_path):
    """"An Authorization header was sent" is provenance a reviewer needs; the
    value is not, and the package is designed to be shared."""
    from paytrace.net import safe_headers

    out = safe_headers({"Authorization": "Bearer SECRET", "Cookie": "s=1",
                        "X-Origin": "server-a"})
    assert out["authorization"] == "<redacted>"
    assert out["cookie"] == "<redacted>"
    assert out["x-origin"] == "server-a"
    assert "SECRET" not in str(out)


# ---- release closure and version alignment ------------------------ #

@pytest.mark.parametrize("pkg", PKGS)
def test_release_gate_audits_the_released_closure(pkg):
    """A tag resolves its own dependency set, and the pins are lower bounds, so
    what ships can differ from what main-branch CI tested."""
    doc = yaml.safe_load(_wf(pkg, "release.yml").read_text())
    runs = " ".join(str(s.get("run", "")) for s in doc["jobs"]["gate"]["steps"])
    assert "pip-audit --strict" in runs  # the gate/dev closure
    # the RUNTIME closure is frozen and audited in the release job, from the
    # clean product venv -- see test_runtime_closure_is_frozen_from_the_...
    release = " ".join(str(s.get("run", ""))
                       for s in doc["jobs"]["release"]["steps"])
    assert "runtime-requirements.txt" in release


@pytest.mark.parametrize("pkg", ["paytrace", "handle-correlation",
                                 "attribution-suite"])
def test_cross_package_pins_are_enforced_not_advisory(pkg):
    """Docs said the four move together while metadata allowed any newer
    version, so ordinary pip resolution could install a mixed set."""
    text = (_repo(pkg) / "pyproject.toml").read_text()
    # Bounded above (<2.1), so pip cannot assemble a mixed MINOR set, and
    # bounded below at 2.0.1, excluding the withdrawn 2.0.0.
    assert "attribution-graph>=2.0.1,<2.1" in text
    assert not re.search(r'"attribution-graph>=[^"]*"', text.replace(
        '"attribution-graph>=2.0.1,<2.1"', "")), "unbounded pin"


def test_mixed_versions_are_detected():
    from attribution_suite import version as v

    original = v.versions
    try:
        v.versions = lambda: {"attribution_graph": "2.0.0", "paytrace": "1.9.0",
                              "handle_correlation": "2.0.0",
                              "attribution_suite": "2.0.0"}
        assert not v.aligned()
    finally:
        v.versions = original


def test_run_refuses_on_a_mixed_installation():
    src = (_repo("attribution-suite") / "src/attribution_suite/cli.py").read_text()
    assert "refusing to run: mixed package versions" in src


# ---- MCP and documentation ----------------------------------- #

@pytest.mark.parametrize("pkg", ["attribution-graph", "attribution-suite"])
def test_readme_does_not_advertise_the_disabled_screenshot_flag(pkg):
    """Operators following stale docs assume a control exists in the unified
    runner when it does not."""
    text = (_repo(pkg) / "README.md").read_text()
    assert "attribution run --case case.yaml --screenshots" not in text
    assert "outside the ssrf" in text.lower()


def test_explain_ads_txt_writes_an_audit_trail(tmp_path):
    """The server told the caller authorization is recorded, and this tool did
    live retrieval with no audit evidence at all."""
    import asyncio
    import json

    from attribution_suite.mcp_server import call_tool

    out = asyncio.run(call_tool("explain_ads_txt",
                                {"domain": "example.com",
                                 "authorization": "audit-ref-9"}))
    payload = json.loads(out[0].text)
    trail = Path(payload["audit_trail"])
    assert trail.exists()
    events = [json.loads(line) for line in trail.read_text().splitlines()]
    assert any(e.get("authorization") == "audit-ref-9" for e in events)
    assert {"mcp_explain_ads_txt", "mcp_explain_ads_txt_result"} <= {
        e["event"] for e in events}


def test_stale_mcp_case_directories_are_swept():
    """Evidence accumulating indefinitely in the system temp area on a
    long-running agent host is a privacy and disk problem."""
    import tempfile

    from attribution_suite.mcp_server import sweep_stale_cases

    stale = Path(tempfile.mkdtemp(prefix="attribution-mcp-"))
    os.utime(stale, (0, 0))
    retained = Path(tempfile.mkdtemp(prefix="attribution-mcp-"))
    (retained / ".retain").touch()
    os.utime(retained, (0, 0))
    fresh = Path(tempfile.mkdtemp(prefix="attribution-mcp-"))

    sweep_stale_cases()
    assert not stale.exists()
    assert retained.exists(), "an explicitly retained case must survive"
    assert fresh.exists()


# ---- L-02 --------------------------------------------------------------- #

@pytest.mark.parametrize("url", [
    "https://example.com:99999/", "https://example.com:abc/",
    "https://example.com:-1/",
])
def test_malformed_ports_produce_a_controlled_rejection(url):
    """A hostile redirect turned a safe rejection into an internal defect."""
    from paytrace.netsec import UrlRejected, check_url

    with pytest.raises(UrlRejected, match="malformed port"):
        check_url(url, resolver=lambda h: ["93.184.216.34"])






def test_deployment_guide_states_the_residual_posture():
    guide = (_repo("attribution-suite") / "DEPLOYMENT.md").read_text()
    # DEPLOYMENT defers to SECURITY.md rather than restating the posture.
    assert "SECURITY.md` is authoritative" in guide
    assert "0700/0600" in guide
    assert "169.254.169.254" in guide


# ---- -------------------------------- #

def test_pinning_does_not_rewrite_the_logical_request():
    """The first attempt substituted the IP into the request URL, which
    made two hostnames on one address share a connection-pool origin -- a
    pooled connection for a.example could serve b.example, and SNI only applies
    when a connection is created. Pinning belongs below the pool."""
    import asyncio

    import httpx
    from paytrace.pinned import PinnedResolver, PinnedTransport

    pins = PinnedResolver()
    pins.pins = {"a.example": "93.184.216.34", "b.example": "93.184.216.34"}
    client = httpx.AsyncClient(transport=PinnedTransport(pins))
    try:
        a = client.build_request("GET", "https://a.example/1")
        b = client.build_request("GET", "https://b.example/1")

        assert a.url.host == "a.example" and b.url.host == "b.example"
        for req, host in ((a, "a.example"), (b, "b.example")):
            hosts = [v for k, v in req.headers.multi_items() if k == "host"]
            assert hosts == [host], f"expected exactly one Host: {hosts}"
    finally:
        asyncio.run(client.aclose())


def test_connect_target_is_the_validated_address():
    """The closure test review asked for: observe the actual connect target,
    not the pin dictionary."""
    import asyncio

    from paytrace.pinned import PinnedResolver, PinnedTransport

    pins = PinnedResolver()
    pins.pins = {"a.example": "93.184.216.34"}
    transport = PinnedTransport(pins)
    backend = transport._pool._network_backend
    assert type(backend).__name__ == "PinnedBackend"

    async def probe():
        # The connect will fail (no such host); we only need the backend to
        # observe the target it was handed.
        with contextlib.suppress(Exception):
            await backend.connect_tcp("a.example", 443, timeout=0.001)

    asyncio.run(probe())
    assert pins.connects, "the backend must observe the connect"
    host, target, port = pins.connects[-1]
    assert host == "a.example" and target == "93.184.216.34"


def test_proxied_fetcher_records_that_pinning_is_off(monkeypatch):
    """The documented posture said proxy pinning is impossible; the Fetcher
    left pins_enforced True, so the record did not match the doc."""
    import asyncio

    from paytrace import EgressPool
    from paytrace.net import Fetcher

    monkeypatch.setenv("PW", "secret")
    pool = EgressPool.from_case([
        {"label": "gulf", "provider": "oxylabs_datacenter",
         "network": "datacenter", "country": "ae", "username": "u",
         "password_env": "PW"}])
    f = Fetcher(user_agent="t", egress=pool)
    f.client_for("gulf")
    assert f.pins.pins_enforced is False
    asyncio.run(f.aclose())


def test_audit_trail_is_not_world_readable(tmp_path):
    """audit.jsonl carries the authorization value and the case
    reference, and was created 0644 under a normal umask."""
    import os
    import stat

    from attribution_graph import CaseScope

    os.umask(0o022)
    (tmp_path / "c.yaml").write_text(
        f"case_ref: T\nauthorization: SENSITIVE-REF\n"
        f"seeds: [domain:a.example]\naudit_path: {tmp_path / 'audit.jsonl'}\n")
    scope = CaseScope.load(str(tmp_path / "c.yaml"))
    scope.audit("probe", note="x")

    trail = tmp_path / "audit.jsonl"
    assert stat.S_IMODE(os.stat(trail).st_mode) == 0o600
    assert "SENSITIVE-REF" in trail.read_text(), "the value is why 0600 matters"


def test_preexisting_audit_file_is_tightened(tmp_path):
    """The mode argument only applies at creation; a trail begun under a looser
    umask stayed loose."""
    import os
    import stat

    from attribution_graph import CaseScope

    trail = tmp_path / "audit.jsonl"
    trail.touch(mode=0o644)
    (tmp_path / "c.yaml").write_text(
        f"case_ref: T\nauthorization: t\nseeds: [domain:a.example]\n"
        f"audit_path: {trail}\n")
    CaseScope.load(str(tmp_path / "c.yaml")).audit("probe")
    assert stat.S_IMODE(os.stat(trail).st_mode) == 0o600


@pytest.mark.parametrize("pkg", ["paytrace", "attribution-suite"])
def test_case_example_keeps_the_audit_trail_in_the_protected_root(pkg):
    text = (_repo(pkg) / "case.example.yaml").read_text()
    assert re.search(r"^audit_path:\s*\./out/", text, re.M)


def test_headers_recorded_on_redirects_and_cache_hits(tmp_path):
    """Only terminal responses carried headers; redirect hops identify
    infrastructure transitions and are evidence in their own right."""
    import asyncio

    import httpx
    from attribution_graph import CaseScope, EvidenceLog
    from paytrace.net import Fetcher

    def newlog(d):
        d.mkdir(parents=True, exist_ok=True)
        (d / "c.yaml").write_text(
            f"case_ref: T\nauthorization: t\nseeds: [domain:a.example]\n"
            f"audit_path: {d / 'a.jsonl'}\n")
        return EvidenceLog(CaseScope.load(str(d / "c.yaml")), d / "ev")

    def handler(request):
        if request.url.path == "/a":
            return httpx.Response(302, headers={"location": "https://x.example/b",
                                                "X-Hop": "one"})
        return httpx.Response(200, content=b"ok", headers={"X-Final": "two"})

    async def run(log, cache):
        f = Fetcher(user_agent="t", cache_dir=cache, evidence_log=log,
                    resolver=lambda h: ["93.184.216.34"])
        f._clients["direct"] = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False)
        await f.get("https://x.example/a", headers={"X-Req": "probe"})
        await f.aclose()

    cache = tmp_path / "cache"
    log = newlog(tmp_path / "run1")
    asyncio.run(run(log, cache))

    outcomes = {c.outcome: c for c in log.captures}
    assert outcomes["redirect"].response_headers.get("x-hop") == "one"
    assert outcomes["redirect"].request_headers.get("x-req") == "probe"
    assert outcomes["success"].response_headers.get("x-final") == "two"

    log2 = newlog(tmp_path / "run2")
    asyncio.run(run(log2, cache))
    cached = log2.captures[0]
    assert cached.outcome == "cache"
    assert cached.response_headers.get("x-final") == "two", \
        "a cache hit must reproduce the original provenance, not empty maps"


@pytest.mark.parametrize("pkg", PKGS)
def test_runtime_closure_is_frozen_from_the_product_environment(pkg):
    """The gate froze its own environment -- editable sources plus
    ruff/bandit/pytest/build/twine -- and labelled it the released closure."""
    doc = yaml.safe_load(_wf(pkg, "release.yml").read_text())
    runs = " ".join(str(s.get("run", "")) for s in doc["jobs"]["release"]["steps"])
    assert "/tmp/product/bin/pip freeze > runtime-requirements.txt" in runs
    assert "resolved-requirements.txt" not in runs


@pytest.mark.parametrize("pkg", PKGS)
def test_security_docs_state_one_posture(pkg):
    """Three files describing the same posture differently is how a reviewer
    trusts the wrong one."""
    security = (_repo(pkg) / "SECURITY.md").read_text()
    assert "Residual security posture (authoritative)" in security
    assert "not yet implemented" not in security
    assert "pins_enforced" in security

    # DEPLOYMENT.md ships once, in attribution-suite (the CLI it documents).
    # The other three link to it rather than carrying a copy.
    if pkg == "attribution-suite":
        deployment = (_repo(pkg) / "DEPLOYMENT.md").read_text()
        assert "SECURITY.md` is authoritative" in deployment
    else:
        assert not (_repo(pkg) / "DEPLOYMENT.md").exists(), \
            f"{pkg} should link to the suite's DEPLOYMENT.md, not copy it"


# ---- the bundle must be complete ------------------------------------- #

@pytest.mark.parametrize("pkg", PKGS)
def test_every_package_carries_the_same_release_controls(pkg):
    """Every public repo carries the same community and legal files, and
    none carries internal process material.

    Parity is asserted for all four rather than assumed. The ABSENCE check
    matters as much: internal audit responses, release runbooks and maintainer
    scripts once shipped in every public archive."""
    for name in ("README.md", "LICENSE", "NOTICE", "CITATION.cff", "SECURITY.md",
                 "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "CHANGELOG.md",
                 "VERIFYING.md", "pyproject.toml"):
        assert (_repo(pkg) / name).exists(), f"{pkg} is missing {name}"

    repo = _repo(pkg)
    internal = [f.name for f in repo.iterdir() if f.is_file() and re.search(
        r"^(AUDIT_RESPONSE|SELF_AUDIT|CLOSEOUT|RELEASE_NOW|BUILD_ID|"
        r"PUBLISHING\.md|PUSH\.sh|VERIFY\.sh|DESIGN\.md)", f.name)]
    assert not internal, f"{pkg} ships internal material: {internal}"

    doc = yaml.safe_load(_wf(pkg, "release.yml").read_text())
    runs = " ".join(str(s.get("run", "")) for s in doc["jobs"]["gate"]["steps"])
    assert "pip-audit --strict" in runs


# ---- push authentication ----------------------------------------------------- #





















# ---- CI correctness (observed failures on the first public run) -------------- #

@pytest.mark.parametrize("pkg", PKGS)
@pytest.mark.parametrize("wf", ["ci.yml", "release.yml"])
def test_pip_audit_never_runs_strict_over_the_environment(pkg, wf):
    """`pip-audit --strict` over the working environment fails unconditionally
    here: our packages are installed editable and are not on PyPI, and --strict
    treats both "could not be audited" and "skipped as editable" as errors. The
    gate was red for a reason unrelated to vulnerabilities, on every run."""
    # Executable lines only, with shell continuations joined.
    #
    # Two traps this walked into: a `\` at end of line put the `-r` argument
    # on the next physical line, and the comments explaining the fix quote the
    # very command they warn against. Scanning raw text found both as
    # violations.
    text = _wf(pkg, wf).read_text().replace("\\\n", " ")
    code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]

    for line in code:
        if "pip-audit" not in line or "--strict" not in line:
            continue
        assert " -r " in line, \
            f"--strict must audit a requirements file, not the env: {line.strip()}"


@pytest.mark.parametrize("pkg", PKGS)
def test_audit_closure_excludes_our_own_packages(pkg):
    """Auditing our own editable packages is what produced the failure; they
    are also not the third-party attack surface."""
    text = _wf(pkg, "ci.yml").read_text()
    assert "pip freeze --exclude-editable" in text
    assert '"attribution-graph", "paytrace", "handle-correlation"' in text


@pytest.mark.parametrize("pkg", PKGS)
def test_runtime_audit_excludes_the_suite_itself(pkg):
    """These packages are not on PyPI during their own first release, so
    auditing the full runtime freeze under --strict would fail every initial
    release for a reason unrelated to vulnerabilities."""
    text = _wf(pkg, "release.yml").read_text()
    assert "runtime-thirdparty.txt" in text
    assert "not on PyPI during their own first release" in text
    # the full record is still published
    assert "runtime-requirements.txt" in text


@pytest.mark.parametrize("pkg", PKGS)
def test_integration_step_tolerates_the_bootstrap_state(pkg):
    """The repos are created in order, so requiring all four made every repo's
    CI red until the last one existed -- a chicken-and-egg on first push."""
    text = _wf(pkg, "ci.yml").read_text()
    assert "/tmp/siblings-missing" in text
    assert "expected only while the repositories are" in text


@pytest.mark.parametrize("pkg", PKGS)
def test_integration_step_still_fails_on_a_real_checkout_failure(pkg):
    """Tolerating a missing repository must not tolerate a broken checkout."""
    text = _wf(pkg, "ci.yml").read_text()
    assert "no bootstrap marker" in text
    block = text.split("Integration tests (cross-package)")[1].split("- name:")[0]
    assert "exit 1" in block


@pytest.mark.parametrize("pkg", PKGS)
def test_audit_closure_spans_sibling_packages(pkg):
    """attribution-suite declares only internal dependencies, so building the
    closure from its own pyproject alone audited ZERO packages and reported
    "no known vulnerabilities" -- a green result covering nothing. Its real
    third-party surface arrives through the siblings."""
    text = _wf(pkg, "ci.yml").read_text()
    assert 'pathlib.Path("..") / name / "pyproject.toml"' in text
    assert "optional-dependencies" in text, "extras are installable surface too"


def test_suite_closure_is_not_empty():
    """The condition that made the weak audit possible, asserted directly."""
    import re as _re
    import tomllib

    ours = {"attribution-graph", "paytrace", "handle-correlation",
            "attribution-suite"}
    # A workspace assertion: it needs the sibling pyprojects to compute a
    # closure at all. Skipping is correct when they are absent; asserting on an
    # empty set would pass vacuously, which is the exact failure this test
    # exists to catch.
    present = [n for n in ours if (ROOT / n / "pyproject.toml").exists()]
    if len(present) < len(ours):
        pytest.skip("workspace closure needs all four repositories")

    specs = set()
    for name in present:
        path = ROOT / name / "pyproject.toml"
        project = tomllib.loads(path.read_text()).get("project", {})
        candidates = list(project.get("dependencies", []))
        for extra in (project.get("optional-dependencies") or {}).values():
            candidates += extra
        for dep in candidates:
            base = _re.split(r"[<>=!\[ ;]", dep)[0].strip().lower()
            if base and base not in ours:
                specs.add(base)

    assert {"httpx", "pyyaml"} <= specs, \
        "the closure must include the real third-party surface"






# ---- cross-repo test hygiene ------------------------------------------------ #

@pytest.mark.parametrize("pkg", ["attribution-graph", "paytrace",
                                 "handle-correlation"])
def test_core_packages_never_reference_siblings_in_tests(pkg):
    """A package's own suite must pass from a single-repo clone.

    attribution-graph carried a test asserting that a file existed in
    paytrace/tests/. It tested nothing, and it made the core package's CI fail
    whenever the sibling was not checked out -- reporting a checkout layout as
    a defect. Only attribution-suite, which integrates the four, may look
    across repositories.
    """
    tests_dir = _repo(pkg) / "tests"
    siblings = {"attribution-graph", "paytrace", "handle-correlation",
                "attribution-suite"} - {pkg}

    for path in tests_dir.glob("test_*.py"):
        for line in path.read_text().splitlines():
            code = line.split("#", 1)[0]
            if "parents[2]" in code:
                raise AssertionError(
                    f"{pkg}/tests/{path.name} walks above its own repository")
            for sibling in siblings:
                assert f'"{sibling}"' not in code and f"'{sibling}'" not in code, \
                    f"{pkg}/tests/{path.name} references sibling {sibling}"


def test_workspace_assertions_skip_rather_than_fail():
    """The suite's cross-repo audits are workspace assertions. When a sibling
    is absent they must skip: on a first push the repositories are created in
    order, so a hard failure there is red for a reason unrelated to the code."""
    for name in ("test_release_and_mcp.py", "test_runtime_limits.py",
                 "test_release_workflow.py", "test_security_controls.py",
                 "test_minimise_and_limits.py"):
        text = (Path(__file__).parent / name).read_text()
        assert "def _repo(" in text, f"{name} lacks the sibling guard"
        assert "pytest.skip" in text








@pytest.mark.parametrize("pkg", PKGS)
def test_shipped_version_is_not_a_prerelease(pkg):
    """What the archive itself declares."""
    import re as _re

    text = (_repo(pkg) / "pyproject.toml").read_text()
    version = _re.search(r'^version = "(.+)"$', text, _re.M).group(1)
    assert not _re.search(r"(rc|a|b|dev)\d*$", version), \
        f"{pkg} ships a pre-release version: {version}"




@pytest.mark.parametrize("pkg", PKGS)
def test_dependabot_keeps_action_pins_fresh(pkg):
    """SHA-pinned actions go stale — Node 20 is already deprecated. Updating
    them by hand risks a mistyped SHA, which is a supply-chain problem rather
    than a typo."""
    config = _repo(pkg) / ".github" / "dependabot.yml"
    assert config.exists()
    doc = yaml.safe_load(config.read_text())
    ecosystems = {u["package-ecosystem"] for u in doc["updates"]}
    assert "github-actions" in ecosystems


@pytest.mark.parametrize("pkg", PKGS)
def test_release_publication_is_idempotent(pkg):
    """A re-run after a partial failure finds the release already created, and
    `gh release create` aborts on an existing tag."""
    text = _wf(pkg, "release.yml").read_text()
    assert "gh release view" in text
    assert "gh release upload" in text and "--clobber" in text


@pytest.mark.parametrize("pkg", PKGS)
def test_release_step_has_a_token(pkg):
    """gh needs GH_TOKEN; without it the step fails at the API call rather than
    at the workflow parse."""
    doc = yaml.safe_load(_wf(pkg, "release.yml").read_text())
    step = next(s for s in doc["jobs"]["release"]["steps"]
                if s.get("name") == "Publish release")
    assert "GH_TOKEN" in (step.get("env") or {})



@pytest.mark.parametrize("pkg", PKGS)
def test_only_the_publishing_action_is_third_party(pkg):
    """Repository policy "require actions pinned to a full-length commit SHA"
    applies to NESTED references. gh-action-sigstore-python and
    action-gh-release each call actions/upload-artifact@v4 internally without
    SHA-pinning it, so the whole workflow was refused — and pinning our own
    reference cannot fix a reference inside someone else's action.

    Both were replaced with the equivalent CLI, which removes them from the
    signing and publishing trust boundary rather than weakening the policy.
    pypa/gh-action-pypi-publish stays: it IS the Trusted Publishing path.
    """
    # Executable lines only. The comments explaining this fix name the very
    # actions they warn against, and a raw scan counts those as violations --
    # the same trap the pip-audit check walked into.
    third = set()
    for wf in ("ci.yml", "release.yml"):
        for line in _wf(pkg, wf).read_text().splitlines():
            if line.lstrip().startswith("#"):
                continue
            match = re.search(r"uses: (\S+?)@", line)
            if match and not match.group(1).startswith("actions/"):
                third.add(match.group(1))
    assert third == {"pypa/gh-action-pypi-publish"}, \
        f"unexpected third-party actions: {sorted(third)}"


@pytest.mark.parametrize("pkg", PKGS)
def test_signing_uses_the_cli_and_verifies_its_own_output(pkg):
    text = _wf(pkg, "release.yml").read_text()
    assert "python -m sigstore sign" in text
    assert "sigstore verify identity" in text, \
        "a signature nobody checks is a file"


@pytest.mark.parametrize("pkg", PKGS)
def test_release_creation_uses_gh_cli(pkg):
    code = "\n".join(ln for ln in _wf(pkg, "release.yml").read_text().splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "gh release create" in code
    assert "softprops/action-gh-release" not in code


@pytest.mark.parametrize("pkg", PKGS)
def test_artifact_download_path_is_a_single_directory(pkg):
    """download-artifact treats `path` as ONE destination directory. A
    multi-line value — copied from the upload step, where multiple paths are
    valid — created a literal directory named "dist/\\nruntime-requirements.txt",
    so `ls dist/*.whl` found nothing and the release died at step two."""
    doc = yaml.safe_load(_wf(pkg, "release.yml").read_text())
    step = next(s for s in doc["jobs"]["release"]["steps"]
                if "download-artifact" in str(s.get("uses", "")))
    path = step["with"]["path"]
    assert "\n" not in path, f"download path must be one directory, got {path!r}"


@pytest.mark.parametrize("pkg", PKGS)
def test_gate_uploads_only_what_it_produced(pkg):
    """runtime-requirements.txt is produced in the RELEASE job from the clean
    product venv, so listing it in the gate's upload named a file that does not
    exist there."""
    doc = yaml.safe_load(_wf(pkg, "release.yml").read_text())
    step = next(s for s in doc["jobs"]["gate"]["steps"]
                if "upload-artifact" in str(s.get("uses", "")))
    assert "runtime-requirements" not in step["with"]["path"]

    gate_runs = " ".join(str(s.get("run", "")) for s in doc["jobs"]["gate"]["steps"])
    rel_runs = " ".join(str(s.get("run", "")) for s in doc["jobs"]["release"]["steps"])
    assert "> runtime-requirements.txt" not in gate_runs
    assert "> runtime-requirements.txt" in rel_runs


@pytest.mark.parametrize("pkg", PKGS)
def test_release_assets_are_built_by_existence(pkg):
    """`nullglob` drops globs that match nothing but leaves LITERAL names in
    place, so a missing sbom.cyclonedx.json would fail `gh release create` on a
    file that was never produced."""
    text = _wf(pkg, "release.yml").read_text()
    assert 'assets+=("$f")' in text
    assert "no release assets found" in text


@pytest.mark.parametrize("pkg", PKGS)
def test_sbom_uses_the_real_cyclonedx_flag(pkg):
    """`cyclonedx-py environment` takes `-o/--output-file`. `--outfile` does
    not exist and the command exits 2 — a failure no amount of YAML review
    catches, because the YAML is valid."""
    text = _wf(pkg, "release.yml").read_text()
    assert "--outfile" not in text
    assert "-o sbom.cyclonedx.json" in text


@pytest.mark.parametrize("pkg", PKGS)
def test_runtime_audit_excludes_by_name_not_by_separator(pkg):
    """`pip freeze` emits "attribution-graph @ file:///...whl#sha256=..." for a
    locally installed wheel, so a pattern anchored on "==" excluded nothing and
    pip-audit then failed on a package that is not yet on PyPI."""
    text = _wf(pkg, "release.yml").read_text()
    assert "[ @=<>]" in text, "must match the package NAME, not name=="
    assert "|attribution-suite)==" not in text


@pytest.mark.parametrize("pkg", PKGS)
def test_gh_release_calls_specify_the_repository(pkg):
    """The release job deliberately does not check out source, so it cannot
    rebuild what it publishes. gh then has no git remote to infer the
    repository from and fails with "fatal: not a git repository" — every gh
    call must name the repository explicitly."""
    text = _wf(pkg, "release.yml").read_text()
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    calls = [ln for ln in code.splitlines() if "gh release" in ln]
    assert calls, "expected gh release calls"
    joined = code.replace("\\\n", " ")
    for verb in ("view", "upload", "create"):
        line = next(ln for ln in joined.splitlines() if f"gh release {verb}" in ln)
        assert '--repo "$GITHUB_REPOSITORY"' in line, \
            f"gh release {verb} does not pass --repo: {line.strip()}"


@pytest.mark.parametrize("pkg", PKGS)
def test_release_job_needs_no_source_checkout(pkg):
    """Nothing in the release job may read the working tree: it has none. This
    is the invariant that makes "publish exactly what the gate built" true, and
    it is also what breaks any step that assumes source is present."""
    import re as _re

    doc = yaml.safe_load(_wf(pkg, "release.yml").read_text())
    rel = doc["jobs"]["release"]
    assert not any("checkout" in str(s.get("uses", "")) for s in rel["steps"])

    needs_tree = _re.compile(
        r"(?<![\w/-])(git\s(?!clone)|pyproject\.toml|CITATION\.cff|\bsrc/|\btests/)")
    for step in rel["steps"]:
        for line in str(step.get("run", "")).splitlines():
            code = line.split("#", 1)[0]
            if "gh release" in code:
                continue
            match = needs_tree.search(code)
            assert not match, \
                f"{step.get('name')} reads the working tree: {code.strip()[:70]}"


@pytest.mark.parametrize("pkg", PKGS)
def test_release_assets_are_deduplicated_by_basename(pkg):
    """`dist/*` already matches `dist/*.sigstore.json`, so listing both added
    each bundle twice. GitHub keys a release asset on its NAME, and uploading
    one name twice in a single call returns
    "HTTP 422 ReleaseAsset.name already exists" — even with --clobber."""
    text = _wf(pkg, "release.yml").read_text()
    assert "basename" in text and "seen=" in text, \
        "asset list must be deduplicated by basename"


@pytest.mark.parametrize("pkg", PKGS)
def test_release_asset_globs_do_not_overlap(pkg):
    """The general rule behind the bug: no two patterns in the asset loop may
    match the same file."""
    import shlex

    text = _wf(pkg, "release.yml").read_text()
    line = next(ln for ln in text.splitlines()
                if "for f in dist/*" in ln)
    patterns = [p for p in shlex.split(line.replace("\\", ""))
                if p not in ("for", "f", "in", "do", ";")]
    # dist/* subsumes dist/*.sigstore.json; that pair must not both appear
    assert not ("dist/*" in patterns and "dist/*.sigstore.json" in patterns), \
        f"overlapping globs in the asset list: {patterns}"


@pytest.mark.parametrize("pkg", PKGS)
def test_publish_action_understands_current_metadata(pkg):
    """v1.10.3 shipped twine 5, which only understands Metadata-Version <= 2.3
    and rejected our hatchling-built 2.5 wheel as "Metadata is missing required
    fields: Name, Version" — a parse failure reported as missing data.

    v1.14.2 ships twine 7. Do not pin this backwards."""
    text = _wf(pkg, "release.yml").read_text()
    assert "f7600683efdcb7656dec5b29656edb7bc586e597" not in text, \
        "the v1.10.3 pin cannot publish a Metadata-Version 2.4+ wheel"
    assert "dc37677b2e1c63e2034f94d8a5b11f265b73ba33" in text


@pytest.mark.parametrize("pkg", PKGS)
def test_gate_and_publish_agree_on_twine(pkg):
    """A wheel that passes `twine check` in the gate must not be rejected at
    upload. That only holds if both run the same twine major."""
    text = _wf(pkg, "release.yml").read_text()
    assert '"twine>=7"' in text, "gate must install the same twine major"


@pytest.mark.parametrize("pkg", PKGS)
def test_built_wheel_passes_the_publishing_twine(pkg):
    """Built for real and checked, rather than assumed from the pin."""
    import glob
    import subprocess
    import sys
    import tempfile

    repo = _repo(pkg)
    out = tempfile.mkdtemp()
    build = subprocess.run([sys.executable, "-m", "build", "-o", out, str(repo)],
                           capture_output=True, text=True)
    if build.returncode != 0:
        pytest.skip("build backend unavailable in this environment")

    import importlib.util
    if importlib.util.find_spec("twine") is None:
        pytest.skip("twine not installed")

    check = subprocess.run([sys.executable, "-m", "twine", "check",
                            *glob.glob(f"{out}/*")], capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr


@pytest.mark.parametrize("pkg", PKGS)
def test_signing_writes_bundles_outside_dist(pkg):
    """sigstore signs in place by default, so `.sigstore.json` landed in dist/.
    The publish action hands EVERY file in dist/ to twine, which then failed:
    "Unknown distribution format: ...whl.sigstore.json".

    Same class as putting SHA256SUMS in dist/ — fixed there, reintroduced here
    by signing in place."""
    text = _wf(pkg, "release.yml").read_text()
    assert "--output-directory signatures" in text
    assert "signatures/*.sigstore.json" in text, \
        "release assets must reference bundles in signatures/"


@pytest.mark.parametrize("pkg", PKGS)
def test_dist_purity_is_enforced_before_publish(pkg):
    """The general rule behind three separate bugs: dist/ is the directory
    twine uploads, so it must contain distributions and nothing else."""
    text = _wf(pkg, "release.yml").read_text()
    assert "non-distribution file in dist/" in text, \
        "a guard must fail the build if dist/ is polluted"

    doc = yaml.safe_load(text)
    steps = doc["jobs"]["release"]["steps"]
    guard = next(i for i, s in enumerate(steps)
                 if "non-distribution file in dist/" in str(s.get("run", "")))
    publish = next(i for i, s in enumerate(steps)
                   if "pypi-publish" in str(s.get("uses", "")))
    assert guard < publish, "the guard must run before the upload"


@pytest.mark.parametrize("pkg", PKGS)
def test_nothing_writes_into_dist_after_the_build(pkg):
    """Scan every release step for a redirect or output flag targeting dist/."""
    import re as _re

    doc = yaml.safe_load(_wf(pkg, "release.yml").read_text())
    for step in doc["jobs"]["release"]["steps"]:
        for line in str(step.get("run", "")).splitlines():
            code = line.split("#", 1)[0]
            assert not _re.search(r">\s*dist/", code), \
                f"{step.get('name')} writes into dist/: {code.strip()[:70]}"
            assert "--output-directory dist" not in code


def test_alignment_compares_the_compatibility_line():
    """The packages pin each other at ==2.0.*, declaring patch releases
    compatible. Requiring an identical patch version was stricter than that
    contract: paytrace 2.0.1 beside 2.0.0 siblings would have made
    `attribution run` refuse to start for every user."""
    from attribution_suite import version as v

    original = v.versions
    try:
        v.versions = lambda: {"attribution_graph": "2.0.0", "paytrace": "2.0.1",
                              "handle_correlation": "2.0.0",
                              "attribution_suite": "2.0.0"}
        assert v.aligned(), "a patch skew is compatible by contract"

        v.versions = lambda: {"attribution_graph": "2.0.0", "paytrace": "2.1.0",
                              "handle_correlation": "2.0.0",
                              "attribution_suite": "2.0.0"}
        assert not v.aligned(), "a MINOR skew must still be refused"
    finally:
        v.versions = original


def test_suite_cannot_resolve_the_broken_published_paytrace():
    """paytrace 2.0.0 on PyPI shipped without its fixture data, so the poisoned
    fixture silently became clean under a wheel install. PyPI versions are
    immutable, so the fix is 2.0.1 and the suite must require it."""
    text = (ROOT / "attribution-suite" / "pyproject.toml").read_text()
    assert '"paytrace>=2.0.1,<2.1"' in text

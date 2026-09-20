"""Regressions for the technical audit (H-01..H-04, M-01..M-05, L-01..L-02)."""

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


# ---- H-01: DNS rebinding ---------------------------------------------------- #

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


# ---- H-02: filesystem modes -------------------------------------------------- #

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


# ---- H-03: SHA-pinned actions ------------------------------------------------ #

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


# ---- H-04: header provenance ------------------------------------------------- #

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


# ---- M-01/M-02: release closure and version alignment ------------------------ #

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
    assert "attribution-graph==2.0.*" in text
    assert "attribution-graph>=" not in text


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


# ---- M-03/M-04/M-05: MCP and documentation ----------------------------------- #

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


# ---- L-01/L-02 --------------------------------------------------------------- #

@pytest.mark.parametrize("url", [
    "https://example.com:99999/", "https://example.com:abc/",
    "https://example.com:-1/",
])
def test_malformed_ports_produce_a_controlled_rejection(url):
    """A hostile redirect turned a safe rejection into an internal defect."""
    from paytrace.netsec import UrlRejected, check_url

    with pytest.raises(UrlRejected, match="malformed port"):
        check_url(url, resolver=lambda h: ["93.184.216.34"])


def test_verify_bootstraps_into_a_disposable_environment():
    """It installed editable packages and tooling into the caller's
    environment, which is neither reproducible nor polite."""
    script = (_repo("attribution-suite") / "VERIFY.sh").read_text()
    assert "python3 -m venv" in script
    assert "VERIFY_IN_PLACE" in script, "an explicit opt-out, not the default"


def test_publishing_guide_covers_sha_pinning_policy():
    guide = (_repo("attribution-suite") / "PUBLISHING.md").read_text()
    assert "full-length commit SHA" in guide
    assert "resolved-requirements.txt" in guide


def test_deployment_guide_states_the_residual_posture():
    guide = (_repo("attribution-suite") / "DEPLOYMENT.md").read_text()
    # DEPLOYMENT defers to SECURITY.md rather than restating the posture.
    assert "SECURITY.md` is authoritative" in guide
    assert "0700/0600" in guide
    assert "169.254.169.254" in guide


# ---- reassessment: R-H01, R-H02, R-M01, R-M02 -------------------------------- #

def test_pinning_does_not_rewrite_the_logical_request():
    """R-H01. The first attempt substituted the IP into the request URL, which
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
    """The closure test the audit asked for: observe the actual connect target,
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
    """R-H02. audit.jsonl carries the authorization value and the case
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
    """R-M01. Only terminal responses carried headers; redirect hops identify
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
    """R-M02. The gate froze its own environment -- editable sources plus
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

    deployment = (_repo(pkg) / "DEPLOYMENT.md").read_text()
    assert "SECURITY.md` is authoritative" in deployment


# ---- R-H03: the bundle must be complete ------------------------------------- #

@pytest.mark.parametrize("pkg", PKGS)
def test_every_package_carries_the_same_release_controls(pkg):
    """handle-correlation was omitted from a submission, so its controls could
    not be attested. Parity is asserted for all four rather than assumed."""
    for name in ("LICENSE", "NOTICE", "CITATION.cff", "SECURITY.md",
                 "DEPLOYMENT.md", "PUBLISHING.md", "VERIFY.sh", "PUSH.sh"):
        assert (_repo(pkg) / name).exists(), f"{pkg} is missing {name}"

    doc = yaml.safe_load(_wf(pkg, "release.yml").read_text())
    runs = " ".join(str(s.get("run", "")) for s in doc["jobs"]["gate"]["steps"])
    assert "pip-audit --strict" in runs


# ---- push authentication ----------------------------------------------------- #

def test_push_connects_git_to_gh_credentials():
    """`gh auth login` does not configure git. Plain `git push` over HTTPS then
    prompts for a password GitHub has not accepted since August 2021, so a
    correct password fails and reads as a wrong one."""
    script = (_repo("attribution-suite") / "PUSH.sh").read_text()
    assert "gh auth setup-git" in script
    setup = script.index("gh auth setup-git")
    push = script.index("git push -u origin main")
    assert setup < push, "credentials must be wired before the first push"


def test_push_supports_ssh_as_an_alternative():
    script = (_repo("attribution-suite") / "PUSH.sh").read_text()
    assert "GIT_PROTOCOL" in script
    assert "git@github.com:" in script


def test_push_failure_message_explains_the_password_trap():
    script = (_repo("attribution-suite") / "PUSH.sh").read_text()
    assert "August 2021" in script
    assert "PASSWORD field" in script or "password field" in script.lower()


def test_publishing_guide_documents_the_auth_step():
    guide = (_repo("attribution-suite") / "PUBLISHING.md").read_text()
    assert "gh auth setup-git" in guide
    assert "GIT_PROTOCOL=ssh" in guide


def test_push_checks_the_workflow_scope_before_committing():
    """These repos ship .github/workflows/, which GitHub protects behind a
    separate token scope that `gh auth login` does not grant by default. The
    push was rejected AFTER the commit was made and the repo created, leaving a
    confusing half-done state."""
    script = (_repo("attribution-suite") / "PUSH.sh").read_text()
    assert "gh auth refresh -h github.com -s workflow" in script

    check = script.index("Requesting the 'workflow' token scope")
    commit = script.index("git commit -q -m")
    assert check < commit, "the scope check must precede any commit"


def test_push_diagnoses_a_workflow_scope_rejection():
    script = (_repo("attribution-suite") / "PUSH.sh").read_text()
    assert 'grep -q "workflow.*scope" /tmp/push.err' in script


def test_publishing_guide_documents_both_token_scopes():
    guide = (_repo("attribution-suite") / "PUBLISHING.md").read_text()
    assert "refusing to allow an OAuth App" in guide
    assert "`repo` and `workflow` scopes" in guide


def test_push_diagnoses_a_divergent_remote():
    """These package names existed at 0.6.0, so a repo can already hold
    unrelated history. A raw non-fast-forward error does not tell an operator
    whether overwriting is safe."""
    script = (_repo("attribution-suite") / "PUSH.sh").read_text()
    assert "fetch first|non-fast-forward" in script
    assert "--force-with-lease origin main" in script
    # never recommend a bare force push
    assert "git push --force origin" not in script

    # Rebase must be the SECONDARY path. Offering it first sends the operator
    # into an add/add conflict on every file, which is what happens when two
    # unrelated histories contain the same project.
    branch = script.index("git branch pre-2.0.0")
    rebase = script.index("--allow-unrelated-histories")
    assert branch < rebase, "preserve-and-replace must come before rebase"
    assert "conflict add/add on every single file" in script

    # and the replace path must be reversible
    assert "git push origin pre-2.0.0" in script


def test_push_normalises_the_remote_protocol():
    """gh picks the protocol from its own git_protocol config, which produced a
    mixed https/ssh remote set across the four repos."""
    script = (_repo("attribution-suite") / "PUSH.sh").read_text()
    assert script.count("git remote set-url origin") >= 3


def test_publishing_guide_covers_the_divergent_remote_case():
    guide = (_repo("attribution-suite") / "PUBLISHING.md").read_text()
    assert "fetch first" in guide
    assert "--force-with-lease" in guide


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


@pytest.mark.parametrize("pkg", PKGS)
def test_build_stamp_ships_with_every_package(pkg):
    """Without it there is no way to tell which build is on disk, so a CI
    failure against a step absent from your source looks like the fix did not
    work rather than like an older commit being deployed."""
    stamp = _repo(pkg) / "BUILD_ID"
    assert stamp.exists()
    text = stamp.read_text()
    assert re.search(r"^build: \d{8}-\d{6}$", text, re.M)
    assert "version: 2.0.0" in text


def test_push_reports_the_build_and_what_it_stages():
    script = (_repo("attribution-suite") / "PUSH.sh").read_text()
    assert "archive build:" in script
    assert "workflow file(s)" in script


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
    for name in ("test_release_blockers.py", "test_signoff_blockers.py",
                 "test_repo_audit_blockers.py", "test_technical_audit.py",
                 "test_final_signoff.py"):
        text = (Path(__file__).parent / name).read_text()
        assert "def _repo(" in text, f"{name} lacks the sibling guard"
        assert "pytest.skip" in text


def test_push_warns_on_a_tag_behind_head():
    """A tag left from an earlier attempt points at that older commit, so
    releasing it publishes code from before the later fixes. Git says nothing
    about this — the release simply ships the wrong tree."""
    script = (ROOT / "attribution-suite" / "PUSH.sh").read_text()
    assert "points behind HEAD" in script
    assert "merge-base --is-ancestor" in script

    warn = script.index("points behind HEAD")
    push = script.index("git push -u origin main")
    assert warn < push, "the warning must precede the push"


def test_publishing_guide_covers_an_existing_tag():
    guide = (ROOT / "attribution-suite" / "PUBLISHING.md").read_text()
    assert "already exists" in guide
    assert "git push origin :refs/tags/" in guide
    # and the case that cannot be fixed by re-tagging
    assert "permanently reserves" in guide


def test_push_refuses_a_leftover_prerelease_version():
    """The rehearsal bumps to 2.0.0rc1. If that is not restored, the real tag
    fails with "tag 2.0.0 does not match packaged version 2.0.0rc1" — AFTER the
    tag exists, so the fix becomes moving a tag rather than editing a file."""
    script = (ROOT / "attribution-suite" / "PUSH.sh").read_text()
    assert "PRE-RELEASE version" in script
    assert "*rc*|*a[0-9]*|*b[0-9]*|*dev*" in script

    check = script.index("PRE-RELEASE version")
    push = script.index("git push -u origin main")
    assert check < push, "the check must precede any push"


@pytest.mark.parametrize("pkg", PKGS)
def test_shipped_version_is_not_a_prerelease(pkg):
    """What the archive itself declares."""
    import re as _re

    text = (_repo(pkg) / "pyproject.toml").read_text()
    version = _re.search(r'^version = "(.+)"$', text, _re.M).group(1)
    assert not _re.search(r"(rc|a|b|dev)\d*$", version), \
        f"{pkg} ships a pre-release version: {version}"


def test_publishing_guide_covers_the_version_mismatch():
    guide = (ROOT / "attribution-suite" / "PUBLISHING.md").read_text()
    assert "does not match packaged version" in guide
    assert "do not skip this" in guide.lower()


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

"""Regressions for the v2.0.0 sign-off blockers B1-B7.

Each asserts the behaviour through the real entry point. The recurring failure
in earlier rounds was testing an adjacent surface: configuration loading rather
than the CLI boundary, schema shape rather than the async handler.
"""

import asyncio
import re
from pathlib import Path
from urllib.parse import quote, quote_plus

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


# ---- B1: MCP under a running event loop ------------------------------------ #

def test_mcp_offloads_run_case_off_the_event_loop():
    """`run_case()` is synchronous and calls asyncio.run() internally. Calling
    it from an async handler while the MCP server owns a loop raises
    "asyncio.run() cannot be called from a running event loop"."""
    src = (_repo("attribution-suite") / "src/attribution_suite/mcp_server.py").read_text()
    assert "asyncio.to_thread(run_case" in src


def test_mcp_handler_runs_inside_a_live_event_loop():
    """The actual async invocation path, exercised. Previous MCP tests read
    source text, so a handler that could only fail at runtime shipped green."""
    from attribution_suite.mcp_server import call_tool

    async def drive():
        # A rejected domain returns before any network work, which is enough to
        # prove the handler is awaitable inside a running loop.
        return await call_tool("attribute_domain",
                               {"domain": "not a domain", "authorization": "test"})

    out = asyncio.run(drive())
    assert out and "not a bare domain" in out[0].text


def test_mcp_rejects_missing_authorization_before_any_work():
    from attribution_suite.mcp_server import call_tool

    out = asyncio.run(call_tool("explain_ads_txt", {"domain": "example.com"}))
    assert "authorization is required" in out[0].text


# ---- B2: concurrency floor at the authoritative boundary ------------------- #

@pytest.mark.parametrize("value", [0, -1])
def test_engine_rejects_non_positive_concurrency(value, tmp_path):
    """Case-file validation covered configuration; `--concurrency 0` is a
    separate input path and reached asyncio.Semaphore(0), where no task can
    acquire a permit and the run hangs."""
    from attribution_graph import CaseScope, Engine

    p = tmp_path / "c.yaml"
    p.write_text(
        f"case_ref: T\nauthorization: t\nseeds: [domain:a.example]\n"
        f"audit_path: {tmp_path / 'a.jsonl'}\n")
    with pytest.raises(ValueError, match="at least 1"):
        Engine(CaseScope.load(str(p)), collectors={}, concurrency=value)


def test_cli_rejects_non_positive_concurrency_before_loading_anything():
    from attribution_suite.cli import main

    assert main(["run", "--case", "/nonexistent.yaml", "--concurrency", "0"]) == 2


# ---- B3: runtime-budget completeness --------------------------------------- #

def test_runtime_exhaustion_is_tracked_and_surfaced():
    """max_runtime_s could stop traversal with frontier work queued while the
    result still reported complete -- a safety limit silently changing the
    answer."""
    import inspect

    from attribution_graph import Engine

    engine_src = inspect.getsource(Engine)
    assert "runtime_exhausted" in engine_src

    runner_src = (_repo("attribution-suite") / "src/attribution_suite/runner.py").read_text()
    assert 'stats["runtime_exhausted"] = True' in runner_src
    # and it must clear completeness, not merely record a flag
    block = runner_src.split('stats["runtime_exhausted"] = True')[1][:200]
    assert 'result_complete"] = False' in block


# ---- B4: RFC 9309 robots semantics ------------------------------------------ #

class _Robots:
    def __init__(self, mode):
        self.mode = mode

    async def get(self, url, allow_html=False):
        class R:
            pass

        if self.mode == "net":
            raise ConnectionError("refused")
        R.status, R.text = {
            "200": (200, "User-agent: *\nDisallow: /secret\n"),
            "404": (404, ""),
            "503": (503, ""),
        }[self.mode]
        return R


@pytest.mark.parametrize("mode,should_fetch", [
    ("200", False),   # explicit Disallow
    ("404", True),    # unavailable: no rules apply (RFC 9309 s2.3.1.3)
    ("503", False),   # unreachable: assume complete disallow (s2.3.1.4)
    ("net", False),   # unreachable
])
def test_respect_policy_follows_rfc9309(mode, should_fetch):
    """Unavailable (4xx) and unreachable (5xx/network) were collapsed into "no
    rules, proceed" -- so a target serving 503 on /robots.txt got crawled under
    a policy named `respect`."""
    from attribution_graph.fetchpolicy import PolicyEngine, RobotsPolicy

    engine = PolicyEngine(policy=RobotsPolicy("respect"), user_agent="t")
    decision = asyncio.run(engine.evaluate(_Robots(mode), "https://x.example/secret"))
    assert decision.should_fetch is should_fetch


def test_record_policy_still_fetches_but_records_the_directive():
    from attribution_graph.fetchpolicy import PolicyEngine, RobotsPolicy

    engine = PolicyEngine(policy=RobotsPolicy("record"), user_agent="t")
    for mode in ("200", "503", "net"):
        d = asyncio.run(engine.evaluate(_Robots(mode), "https://x.example/secret"))
        assert d.should_fetch, mode


def test_robots_cache_is_scheme_aware():
    """http:// and https:// robots.txt are separate resources."""
    import inspect

    from attribution_graph.fetchpolicy import PolicyEngine

    assert "key = (scheme, host)" in inspect.getsource(PolicyEngine._robots_for)


# ---- B5: release topology --------------------------------------------------- #

def test_placeholder_detection_targets_the_sentinel_not_the_owner():
    """Checking for "OWNER" matched OWNERDOMAIN, a real IAB field, and would
    have flagged the substituted handle as a failure."""
    text = (_repo("attribution-suite") / "tests/test_release_blockers.py").read_text()
    assert '"github.com/OWNER/" in' in text


@pytest.mark.parametrize("pkg", PKGS)
def test_sibling_checkout_precedes_sibling_dependent_tests(pkg):
    """A local sibling workspace being green does not prove the hosted
    topology is: the workflow ran sibling-dependent tests before checking the
    siblings out."""
    doc = yaml.safe_load((_repo(pkg) / ".github/workflows/ci.yml").read_text())
    for job, spec in doc["jobs"].items():
        steps = spec.get("steps", [])
        labels = [str(s.get("name") or s.get("run", ""))[:40] for s in steps]
        sibling = [i for i, s in enumerate(labels) if "sibling" in s.lower()]
        tests = [i for i, s in enumerate(labels)
                 if "pytest" in s.lower() or "Integration" in s]
        if sibling and tests:
            assert min(sibling) < min(tests), f"{pkg}:{job} tests before checkout"


# ---- B6: encoded identifiers ------------------------------------------------ #

def test_minimize_scrubs_percent_encoded_identifiers(tmp_path):
    """`Jane Doe` survived as `Jane%20Doe` in a URL provenance field inside an
    otherwise-minimized artifact. Minimisation has to cover the canonical forms
    the pipeline itself produces, or it protects the spelling, not the person."""
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
        f"case_ref: T\nauthorization: t\nseeds: [person_name:Jane Doe]\n"
        f"audit_path: {tmp_path / 'a.jsonl'}\nminimize: true\n"
        "salt: 00112233445566778899aabbccddeeff\n")
    scope = CaseScope.load(str(tmp_path / "c.yaml"))

    g = AttributionGraph(case_ref="T")
    for group in ("analytics", "ads_txt"):
        for who, kind in (("Jane Doe", IdKind.PERSON_NAME), ("jdoe", IdKind.HANDLE)):
            g.add_claim(Claim(
                subject=Identifier(kind, who),
                predicate=Predicate.SHARES_ANALYTICS_ID,
                object=Identifier(IdKind.ANALYTICS_ID, f"ga4:G-{group}"),
                collector="c",
                source_url=f"https://x.example/search?q={quote(who)}",
                reliability=Reliability.AUTHORITATIVE,
                correlation_group=f"{group}|{who}"))

    out = tmp_path / "out"
    write_all(g, resolve(g, {EntityType.PERSON}), scope, out)

    for form in ("Jane Doe", quote("Jane Doe"), quote_plus("Jane Doe"),
                 "Jane_Doe", "jane doe"):
        leaks = [p.name for p in out.iterdir()
                 if form in p.read_text(errors="ignore")]
        assert not leaks, f"{form!r} survives minimisation in {leaks}"


# ---- B7: SBOM provenance ---------------------------------------------------- #

@pytest.mark.parametrize("pkg", PKGS)
def test_sbom_generator_is_isolated_from_the_product_environment(pkg):
    """Installing the wheel and cyclonedx-bom into one environment and
    inventorying it produced an SBOM containing the SBOM tool. A syntactically
    valid SBOM describing the wrong closure is worse than none."""
    text = (_repo(pkg) / ".github/workflows/release.yml").read_text()
    assert "/tmp/product" in text and "/tmp/sbomtool" in text
    assert "cyclonedx-py environment /tmp/product" in text
    assert "SBOM contains tooling" in text, "must fail if tooling leaks in"


@pytest.mark.parametrize("pkg", PKGS)
def test_one_sbom_filename_end_to_end(pkg):
    text = (_repo(pkg) / ".github/workflows/release.yml").read_text()
    assert len(set(re.findall(r"sbom[\w.]*\.json", text))) <= 1


# ---- self-audit findings (S1-S2) -------------------------------------------- #

def test_mcp_does_not_present_a_raw_identifier_as_the_conclusion():
    """S1. On a real run `_top_entity` returned
    `"conclusion": "seller_id:pubmatic.example/156423"` with
    `"band": "UNSUPPORTED"` -- an agent would relay that as the operator's
    identity. `max(entities, key=len)` over singleton clusters returns whatever
    sorts first, and a conclusion paired with UNSUPPORTED is incoherent.
    """
    from attribution_suite.mcp_server import _top_entity

    class Ent:
        def __init__(self, n, label):
            self.identifiers = list(range(n))
            self.best_label = label

    class Res:
        def __init__(self, ents, band):
            self.graph = type("G", (), {"entities": dict(enumerate(ents))})()
            assessment = type("A", (), {
                "log_odds": 5.0,
                "band": type("B", (), {"value": band})(),
                "independent_groups": 2})()
            self.resolution = type("R", (), {"assessments": {1: assessment}})()

    # singleton cluster, unsupported band: no conclusion
    top = _top_entity(Res([Ent(1, "seller_id:pubmatic.example/156423")],
                          "UNSUPPORTED"))
    assert top["resolved"] is False and top["label"] is None
    assert "could not be established" in top["note"]

    # a merged cluster with a real name and a real band: reported
    top = _top_entity(Res([Ent(2, "Example Media Holdings Ltd")],
                          "STRONG_EVIDENCE"))
    assert top["resolved"] is True
    assert top["label"] == "Example Media Holdings Ltd"

    # merged, but the label is a bare identifier key: still no conclusion
    top = _top_entity(Res([Ent(2, "domain:x.example")], "STRONG_EVIDENCE"))
    assert top["resolved"] is False


def test_fetcher_never_writes_a_cache_into_the_working_directory():
    """S2. `cache_dir` defaulted to `Path(".eae-cache")`, so retrieved bytes
    from the subject landed in whatever directory the process started in --
    outside the retention and minimisation boundary the case file defines, and
    left there after the run."""
    import os
    import tempfile
    from pathlib import Path

    from paytrace.net import Fetcher

    here = Path(tempfile.mkdtemp())
    prev = Path.cwd()
    os.chdir(here)
    try:
        f = Fetcher(user_agent="t")
        assert not (here / ".eae-cache").exists()
        assert not list(here.iterdir()), "a bare Fetcher must not touch cwd"
        assert str(f.cache_dir).startswith(tempfile.gettempdir())
    finally:
        os.chdir(prev)


def test_runner_keeps_the_cache_inside_the_case_output():
    """Retrieved content should live and die with the investigation."""
    src = (_repo("attribution-suite") / "src/attribution_suite/runner.py").read_text()
    assert 'cache_dir=Path(out) / ".cache"' in src

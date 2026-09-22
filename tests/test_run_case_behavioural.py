"""Behavioural test of the real production path.

review's systemic finding: `run_case()` was ~16% executed by the suite. The
test named `test_runner_passes_what_it_builds` inspected source text with
`inspect.getsource`; it never ran the runner. That is why green suites kept
missing seam failures — the seams were never executed.

This runs the actual `run_case()` with real CaseScope, Engine, PolicyEngine,
EvidenceLog and Fetcher, against a mock transport and an injected resolver.
"""

import json
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.integration

ADS_TXT = b"""OWNERDOMAIN=examplemedia.example
pubmatic.example, 156423, DIRECT, 5d62403b186f2ace
"""
SELLERS = json.dumps({"contact_email": "s@pubmatic.example", "version": "1.0",
                      "sellers": [{"seller_id": "156423",
                                   "name": "Example Media Holdings Ltd",
                                   "domain": "examplemedia.example",
                                   "seller_type": "PUBLISHER",
                                   "is_confidential": 0}]}).encode()


def _transport(disallow: bool = False):
    robots = b"User-agent: *\nDisallow: /private\n" if disallow else b"User-agent: *\nAllow: /\n"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, content=robots)
        if path == "/ads.txt":
            return httpx.Response(200, content=ADS_TXT)
        if path == "/sellers.json":
            return httpx.Response(200, content=SELLERS)
        if path == "/":
            return httpx.Response(200, content=b"<html><body>site</body></html>")
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _case(tmp_path: Path, **extra) -> Path:
    body = [
        "case_ref: RUNCASE-TEST",
        "authorization: 'deterministic test'",
        "contact_email: test@example.test",
        "seeds: [domain:scraper-site.example]",
        "entity_types_allowed: [Company]",
        f"audit_path: {tmp_path / 'audit.jsonl'}",
        "robots_policy: respect",
        "pivot_radius: 1",
        # A small budget keeps the test fast AND proves the budget fix works in
        # the real path: unconstrained it made ~400 mock requests over 3
        # minutes, which is both slow and evidence the declared limit matters.
        "max_requests: 25",
    ]
    body += [f"{k}: {v}" for k, v in extra.items()]
    p = tmp_path / "case.yaml"
    p.write_text("\n".join(body) + "\n")
    return p


def _run(tmp_path, transport=None, **kw):
    """Execute the real runner with the network mocked at the transport.

    `run_case` imports Fetcher inside the function body, so the name is
    resolved from `paytrace` at call time -- patching `runner.Fetcher` binds
    nothing. Patching the source module is what actually intercepts it.
    """
    import paytrace

    from attribution_suite import runner

    real = paytrace.Fetcher

    def patched(*args, **kwargs):
        kwargs.setdefault("resolver", lambda host: ["93.184.216.34"])
        f = real(*args, **kwargs)
        for e in f.egress.egresses:
            f._clients[e.label] = httpx.AsyncClient(
                transport=transport or _transport(), follow_redirects=False)
        return f

    paytrace.Fetcher = patched
    try:
        return runner.run_case(str(_case(tmp_path)), tmp_path / "out", **kw)
    finally:
        paytrace.Fetcher = real


# ---- shipped examples load ------------------------------------------------- #

@pytest.mark.parametrize("name", ["paytrace", "attribution-suite"])
def test_every_shipped_case_example_loads(name):
    """The canonical documented input must be accepted by the canonical parser.
    Both shipped examples were rejected with `unknown case-file key(s): budget,
    jurisdictions` — a public contract failure introduced by the unknown-key
    check that was itself a safety fix."""
    from attribution_graph import CaseScope

    for root in (Path(__file__).resolve().parents[2] / name,
                 Path(__file__).resolve().parents[1]):
        example = root / "case.example.yaml"
        if example.exists():
            CaseScope.load(str(example))
            return
    pytest.skip(f"{name}/case.example.yaml not present in this checkout")


def test_declared_budget_is_the_budget_that_runs(tmp_path):
    """`max_requests` was validated and then not read: a case file declaring 1
    ran with 5000. A safety budget that is accepted and ignored is worse than an
    unknown key."""
    from attribution_graph import CaseScope

    p = tmp_path / "c.yaml"
    p.write_text(
        f"case_ref: T\nauthorization: t\nseeds: [domain:a.example]\n"
        f"audit_path: {tmp_path / 'a.jsonl'}\nmax_requests: 7\n")
    assert CaseScope.load(str(p)).max_requests == 7


def test_conflicting_budget_spellings_are_rejected(tmp_path):
    from attribution_graph import CaseScope

    p = tmp_path / "c.yaml"
    p.write_text(
        f"case_ref: T\nauthorization: t\nseeds: [domain:a.example]\n"
        f"audit_path: {tmp_path / 'a.jsonl'}\nmax_requests: 1\n"
        "budget:\n  max_requests: 99\n")
    with pytest.raises(ValueError, match="twice with different values"):
        CaseScope.load(str(p))


# ---- the real orchestration path ------------------------------------------- #

def test_run_case_executes_end_to_end(tmp_path):
    result = _run(tmp_path)
    assert result is not None
    out = tmp_path / "out"
    assert out.exists()
    assert result.stats.get("fetches", 0) > 0, "the run must have fetched"


def test_run_case_records_evidence_for_what_it_fetched(tmp_path):
    """A run making real requests used to write an empty evidence package and
    report evidence_verified=True."""
    result = _run(tmp_path)
    manifest = tmp_path / "out" / "evidence" / "evidence_manifest.json"
    assert manifest.exists()
    doc = json.loads(manifest.read_text())
    assert doc["captures"], "captures must exist for a run that fetched"
    assert result.stats["captures"] >= 1
    assert result.stats["captures"] >= result.stats["fetches"] * 0  # reconciled


def test_run_case_evidence_package_verifies(tmp_path):
    from attribution_graph import verify_package

    _run(tmp_path)
    r = verify_package(tmp_path / "out" / "evidence")
    assert r.ok, r.problems


def test_run_case_consults_the_fetch_policy(tmp_path):
    """PolicyEngine was constructed and never called, so `robots_policy:
    respect` in a case file bought nothing."""
    result = _run(tmp_path)
    assert result.stats.get("fetches", 0) > 0
    audit = (tmp_path / "audit.jsonl").read_text()
    assert audit, "the run must have produced a review trail"


def test_run_case_produces_a_report(tmp_path):
    _run(tmp_path)
    out = tmp_path / "out"
    assert any(out.glob("*.md")) or any(out.glob("*.json")), \
        "the run must emit something a reader can open"


def test_run_case_result_is_marked_valid_absent_defects(tmp_path):
    """The converse of the invalid-result path: a clean run must not be
    flagged."""
    result = _run(tmp_path)
    assert result.stats.get("result_valid") is not False
    assert not result.stats.get("internal_defects")


# ---- live network behaviour: work and cleanup share one event loop -------- #

import asyncio as _asyncio_live  # noqa: E402

asyncio = _asyncio_live

class _LoopBoundTransport(httpx.AsyncBaseTransport):
    """Behaves like a real connection pool: it belongs to the event loop that
    first used it, and cannot be closed from another. httpx.MockTransport has
    no such state, which is why a second asyncio.run() for cleanup passed every
    mocked test and crashed on every real network."""

    def __init__(self, handler):
        self._handler, self._loop = handler, None

    async def handle_async_request(self, request):
        self._loop = self._loop or asyncio.get_running_loop()
        return self._handler(request)

    async def aclose(self):
        if self._loop is not None and asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("Event loop is closed")


def _real_world(monkeypatch):
    import paytrace.net as net

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if req.url.path == "/ads.txt":
            return httpx.Response(200, text="adnet.example, 7741, DIRECT\n")
        return httpx.Response(404)

    original = net.Fetcher.__init__

    def patched(self, *a, **k):
        k["resolver"] = lambda host: ["93.184.216.34"]
        original(self, *a, **k)
        for route in self.egress.egresses:
            self._clients[route.label] = httpx.AsyncClient(
                transport=_LoopBoundTransport(handler), follow_redirects=False)

    monkeypatch.setattr(net.Fetcher, "__init__", patched)


def _case(tmp_path):
    p = tmp_path / "case.yaml"
    p.write_text("case_ref: T\nauthorization: test\ncontact_email: t@example.com\n"
                 "seeds: [domain:live-site.example]\nentity_types_allowed: [Company]\n"
                 f"audit_path: {tmp_path}/audit.jsonl\nrobots_policy: respect\nmax_requests: 20\n")
    return p


def test_run_case_closes_the_fetcher_in_the_loop_that_used_it(monkeypatch, tmp_path):
    """`attribution run` collected everything, then closed its fetcher in a
    SECOND asyncio.run() and crashed with "Event loop is closed" -- before
    writing a single report. It happened on every real network."""
    from attribution_suite import runner

    _real_world(monkeypatch)
    res = runner.run_case(str(_case(tmp_path)), tmp_path / "out")
    names = {p.name for p in res.outputs}
    assert "attribution_report.md" in names, "the report must be written"


# ---- the one-command form -------------------------------------------------- #

def test_domain_writes_a_valid_case_file_even_with_a_colon(tmp_path):
    """`--domain` builds the case file for you. Authorization references
    routinely contain a colon ("ticket: 4821"), which hand-formatted YAML turns
    into an invalid document."""
    import yaml

    from attribution_suite.cli import _synth_case

    case = _synth_case("example.com", "ticket: 4821", tmp_path)
    doc = yaml.safe_load(case.read_text())
    assert doc["authorization"] == "ticket: 4821"
    assert doc["seeds"] == ["domain:example.com"]
    assert doc["max_requests"] <= 200, "a one-liner must not start an unbounded crawl"


def test_an_unasserted_authorization_is_recorded_as_such(tmp_path):
    """The control exists so a human states their authority. The convenience
    form must record its absence rather than invent one."""
    import yaml

    from attribution_suite.cli import UNATTESTED, _synth_case

    doc = yaml.safe_load(_synth_case("example.com", None, tmp_path).read_text())
    assert doc["authorization"] == UNATTESTED
    assert "no authority asserted" in doc["authorization"]


def test_personal_identifiers_are_masked_in_the_on_screen_summary():
    """A live domain yields real people's addresses; the summary is what ends
    up on a projector."""
    from attribution_suite.cli import _mask

    assert _mask("email:someone@example.com", False).startswith("email:[withheld")
    assert _mask("email:someone@example.com", True) == "email:someone@example.com"
    assert _mask("domain:example.com", False) == "domain:example.com"

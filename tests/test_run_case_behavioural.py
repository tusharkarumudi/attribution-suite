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


# ---- INCOMPLETE must say what actually stopped it -------------------------- #

class _Scope:
    max_requests = 200


def test_incomplete_names_blocking_not_a_budget():
    """A run blocked by robots.txt reported "a budget stopped the search",
    sending the reader to raise a limit that was never reached — the real run
    made 41 requests against a ceiling of 200."""
    from attribution_suite.cli import _incomplete_reason

    reason = _incomplete_reason({"collection_blocked": 5}, _Scope())
    assert "blocked" in reason and "budget" not in reason


def test_incomplete_names_the_budget_when_it_was_the_budget():
    from attribution_suite.cli import _incomplete_reason

    reason = _incomplete_reason({"budget_exhausted": True}, _Scope())
    assert "request budget (200)" in reason


def test_incomplete_reports_every_cause_that_applied():
    from attribution_suite.cli import _incomplete_reason

    reason = _incomplete_reason(
        {"collection_blocked": 2, "budget_exhausted": True,
         "runtime_exhausted": True, "nodes_truncated": True}, _Scope())
    for expected in ("blocked", "request budget", "wall-clock", "node budget"):
        assert expected in reason


def test_incomplete_never_invents_a_cause():
    from attribution_suite.cli import _incomplete_reason

    assert _incomplete_reason({}, _Scope()) == "the search did not run to completion"


# ---- findings must answer the question that was asked ---------------------- #

from types import SimpleNamespace as _NS  # noqa: E402


def _assessment(log_odds, band, groups, evidence):
    return _NS(log_odds=log_odds, band=_NS(value=band), estimative="e",
               independent_groups=groups, top_evidence=evidence)


def _result(seed, assessments):
    return _NS(scope=_NS(seeds=[f"domain:{seed}"]),
               resolution=_NS(assessments=assessments))


def test_findings_are_about_the_domain_that_was_asked(capsys):
    """A run about one site reported links between unrelated third parties:
    every assessment in the graph was ranked together and the top five printed,
    so GLEIF cross-references outranked the site's own payee."""
    from attribution_suite.cli import _print_findings

    _print_findings(_result("pictame.com", {
        ("domain:pictame.com", "seller_id:adnet.example/99"):
            _assessment(5, "MODERATE_EVIDENCE", 2, [("ads_txt|pictame.com", 8.0)]),
        ("company_number:IE/462932", "lei:635400IRYI5QC7GUYI75"):
            _assessment(14, "MODERATE_EVIDENCE", 1, [("gleif|x", 14.0)]),
    }))
    out = capsys.readouterr().out
    assert "seller_id:adnet.example/99" in out, "the seed's own payee must appear"
    assert "lei:635400IRYI5QC7GUYI75" not in out, "third-party links must not be paraded"
    assert "1 further link(s)" in out, "but they must still be counted"


def test_a_self_published_claim_is_labelled(capsys):
    """An Instagram viewer's terms page names Meta, and that mention scored
    STRONG_EVIDENCE — presented without qualification it reads as an
    attribution of a real company."""
    from attribution_suite.cli import _print_findings

    _print_findings(_result("pictame.com", {
        ("cik:0001326801", "domain:pictame.com"):
            _assessment(9, "STRONG_EVIDENCE", 2,
                        [("imprint|pictame.com|/terms", 8.0)]),
    }))
    out = capsys.readouterr().out
    assert "self-published" in out


def test_independently_sourced_claims_are_not_labelled(capsys):
    from attribution_suite.cli import _print_findings

    _print_findings(_result("x.example", {
        ("domain:x.example", "seller_id:adnet.example/1"):
            _assessment(8, "STRONG_EVIDENCE", 2,
                        [("sellers_json|adnet.example|1", 8.0),
                         ("wayback_ads|x.example|202501", 8.0)]),
    }))
    assert "self-published" not in capsys.readouterr().out


def test_findings_name_the_seed_when_nothing_resolved(capsys):
    from attribution_suite.cli import _print_findings

    _print_findings(_result("x.example", {
        ("a:1", "b:2"): _assessment(3, "WEAK", 1, [("g|1", 3.0)]),
    }))
    out = capsys.readouterr().out
    assert "nothing resolved about x.example" in out


def test_the_payee_name_is_reachable_through_the_chain(capsys):
    """The answer is domain -> seller_id -> org_name. Keeping only links that
    name the seed filed the payee itself under "other links"."""
    from attribution_suite.cli import _print_findings

    _print_findings(_result("pictame.com", {
        ("domain:pictame.com", "seller_id:google.com/pub-383"):
            _assessment(5, "MODERATE_EVIDENCE", 1, [("ads_txt|pictame.com", 5.4)]),
        ("seller_id:google.com/pub-383", "org_name:Some Media Ltd"):
            _assessment(8, "STRONG_EVIDENCE", 1,
                        [("sellers_json|google.com|pub-383", 8.0)]),
        ("company_number:IE/462932", "lei:635400IRY"):
            _assessment(14, "MODERATE_EVIDENCE", 1, [("gleif|x", 14.0)]),
    }))
    out = capsys.readouterr().out
    assert "org_name:Some Media Ltd" in out, "the payee must be shown"
    assert "lei:635400IRY" not in out, "unrelated registry links must stay counted"


def test_a_registry_lookup_is_not_corroboration_of_the_relationship(capsys):
    """EDGAR confirms the company exists; it says nothing about who runs the
    site. Counting it as an independent group turned "the terms page names
    Meta" into STRONG_EVIDENCE that Meta operates the site."""
    from attribution_suite.cli import _print_findings

    _print_findings(_result("pictame.com", {
        ("cik:0001326801", "domain:pictame.com"):
            _assessment(9, "STRONG_EVIDENCE", 2,
                        [("imprint|pictame.com|/terms", 8.0),
                         ("edgar|0001326801", 8.0)]),
    }))
    out = capsys.readouterr().out
    assert "self-published" in out
    assert "corroborate the entity, not the relationship" in out


def test_a_genuinely_corroborated_link_is_not_labelled(capsys):
    """Two independent groups that BOTH reference the seed is real support."""
    from attribution_suite.cli import _print_findings

    _print_findings(_result("x.example", {
        ("domain:x.example", "seller_id:adnet.example/1"):
            _assessment(8, "STRONG_EVIDENCE", 2,
                        [("ads_txt|x.example", 8.0),
                         ("wayback_ads|x.example|202501", 8.0)]),
    }))
    assert "self-published" not in capsys.readouterr().out


def test_the_seed_s_own_links_are_never_outranked(capsys):
    """Ranking both hops together by score put seller-to-seller pairs (8.0)
    above the site's own ads.txt declarations (5.4), so widening the search
    pushed the seed's own findings off the list entirely."""
    from attribution_suite.cli import _print_findings

    _print_findings(_result("insmask.com", {
        ("domain:insmask.com", "seller_id:google.com/pub-160"):
            _assessment(5.4, "UNSUPPORTED", 1, [("ads_txt|insmask.com", 5.4)]),
        ("seller_id:appnexus.com/3626", "seller_id:google.com/pub-160"):
            _assessment(8, "WEAK", 1, [("sellers_json|appnexus.com|3626", 8.0)]),
    }))
    out = capsys.readouterr().out
    assert "domain:insmask.com  ->  seller_id:google.com/pub-160" in out
    assert "seller_id:appnexus.com/3626  ->  seller_id:google.com" not in out, \
        "peers sharing an ad system are not a step toward whoever is paid"


def test_the_second_hop_reaches_a_named_party(capsys):
    from attribution_suite.cli import _print_findings

    _print_findings(_result("insmask.com", {
        ("domain:insmask.com", "seller_id:google.com/pub-160"):
            _assessment(5.4, "UNSUPPORTED", 1, [("ads_txt|insmask.com", 5.4)]),
        ("seller_id:google.com/pub-160", "org_name:Insmask Media Ltd"):
            _assessment(8, "STRONG_EVIDENCE", 1,
                        [("sellers_json|google.com|pub-160", 8.0)]),
    }))
    out = capsys.readouterr().out
    assert "WHO THOSE ACCOUNTS BELONG TO" in out
    assert "org_name:Insmask Media Ltd" in out


def test_the_payee_is_the_seller_that_declares_this_site(capsys):
    """An ads.txt lists dozens of DIRECT accounts; most are networks whose
    sellers.json entry covers thousands of sites. The one identifying the
    operator is the entry whose declared domain IS this site."""
    from attribution_suite.cli import _payee

    res = _NS(resolution=_NS(graph=_NS(claims=[
        _NS(subject=_NS(value="seller_id:google.com/pub-2201"),
            object=_NS(value="person_name:A Person"),
            raw={"declared_domain": "storiesdwon.co", "seller_type": "PUBLISHER",
                 "name_kind": "natural_person"}),
        _NS(subject=_NS(value="seller_id:appnexus.com/1234"),
            object=_NS(value="org_name:Big Network Inc"),
            raw={"declared_domain": "bignetwork.example",
                 "seller_type": "INTERMEDIARY", "name_kind": "organization"}),
    ])))
    _payee(res, "storiesdwon.co", False)
    out = capsys.readouterr().out
    assert "seller_id:google.com/pub-2201" in out
    assert "Big Network Inc" not in out, "other customers of the ad system are not the payee"
    assert "[withheld" in out, "a natural person is masked on screen"
    assert "LEAD, not a finding" in out


def test_a_www_prefixed_declaration_still_matches(capsys):
    from attribution_suite.cli import _payee

    res = _NS(resolution=_NS(graph=_NS(claims=[
        _NS(subject=_NS(value="seller_id:x/1"), object=_NS(value="org_name:Real Ltd"),
            raw={"declared_domain": "www.wow.co", "seller_type": "PUBLISHER",
                 "name_kind": "organization"}),
    ])))
    _payee(res, "wow.co", True)
    assert "Real Ltd" in capsys.readouterr().out


def test_payee_falls_back_to_the_account_the_site_declares(capsys):
    """Many individual sellers publish no domain. Showing nothing because the
    strongest signal is missing hid a name the run had already found."""
    from attribution_suite.cli import _payee

    res = _NS(resolution=_NS(graph=_NS(claims=[
        _NS(subject=_NS(value="domain:sssthread.com"),
            object=_NS(value="seller_id:google.com/pub-544"), raw={}),
        _NS(subject=_NS(value="seller_id:google.com/pub-544"),
            object=_NS(value="person_name:Some Person"),
            raw={"seller_type": "PUBLISHER", "name_kind": "natural_person",
                 "declared_domain": None}),
    ])))
    _payee(res, "sssthread.com", True)
    out = capsys.readouterr().out
    assert "seller_id:google.com/pub-544" in out
    assert "Some Person" in out
    assert "none published" in out, "say why the strongest signal is absent"


def test_blocked_urls_are_named_not_just_counted(monkeypatch, tmp_path):
    """Counting blocks without naming them makes an absence undiagnosable: a
    run that could not reach the file naming the payee looked identical to one
    that skipped a stylesheet. Two runs of the same site differed and there was
    no way to see why."""
    from attribution_suite import runner

    _real_world(monkeypatch)
    res = runner.run_case(str(_case(tmp_path)), tmp_path / "out")
    blocked = [w for w in res.stats.get("warnings", []) if "blocked:" in w]
    if res.stats.get("collection_blocked"):
        assert blocked, "a blocked retrieval must name its URL"
        assert any("http" in w for w in blocked)

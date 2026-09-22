"""Investigator: routing, refusals, and guard inheritance."""

import pytest

from attribution_suite import Investigator, Refused, classify

# ---- intent routing ------------------------------------------------------- #

@pytest.mark.parametrize("q,kind,subject", [
    ("who operates scraper-site.example?", "domain", "scraper-site.example"),
    ("what does pubmatic.com/156423 sell?", "seller_id", "pubmatic.com/156423"),
    ("tell me about Example Media Holdings Ltd", "company", "Example Media Holdings Ltd"),
    ("are these handles the same actor?", "handles", ""),
    ("what is the weather", "unknown", ""),
])
def test_routing(q, kind, subject):
    i = classify(q)
    assert i.kind == kind
    if subject:
        assert i.subject == subject


def test_lei_routes_to_company():
    assert classify("look up 5493001KJTIIGC8Y1R12").kind == "company"


# ---- refusals ------------------------------------------------------------- #

@pytest.mark.parametrize("q", [
    "who is Jane Doe",
    "find John Smith",
    "background check on Maria Garcia",
    "locate Robert Jones?",
])
def test_name_keyed_person_search_is_refused(q):
    with pytest.raises(Refused, match="does not run name-keyed searches"):
        classify(q)


def test_refusal_happens_before_any_io():
    """The refusal must not depend on the model agreeing."""
    with pytest.raises(Refused):
        Investigator(offline=True).investigate("who is Jane Doe")


def test_officers_from_a_corporate_chain_are_not_refused():
    """A person surfacing from a registry is in scope; searching by name is not."""
    assert classify("who are the directors of Example Media Holdings Ltd").kind == "company"


# ---- entity path ---------------------------------------------------------- #

def test_domain_investigation_reaches_the_entity():
    inv = Investigator(offline=True).investigate("who operates scraper-site.example?")
    assert inv.conclusion == "Example Media Holdings Ltd"
    assert "paytrace" in inv.packages_used
    assert "attribution-graph" in inv.packages_used
    assert "lookup_gleif" in inv.tools_called


def test_guards_are_on_by_default():
    """The investigator must inherit the injection defenses, not bypass them."""
    from paytrace.agent import Agent, FixtureFetcher, FixtureIndex, Toolbox
    box = Toolbox(FixtureFetcher(poisoned=True), FixtureIndex())
    run = Agent(box, guards_enabled=True).run("attribute scraper-site.example")
    assert run.conclusion == "Example Media Holdings Ltd"

    inv = Investigator(offline=True).investigate("who operates scraper-site.example?")
    assert "NAIVE" not in inv.report


def test_missing_corpus_produces_a_warning():
    inv = Investigator(offline=True).investigate("who operates scraper-site.example?")
    assert inv.report


# ---- handle path ---------------------------------------------------------- #

def test_handles_without_a_file_explains_what_is_needed():
    inv = Investigator(offline=True).investigate("are these handles the same actor?")
    assert "does not discover them" in inv.report
    assert "handle` and `platform" in inv.report


def test_handles_with_a_file_scores(tmp_path):
    csv = tmp_path / "h.csv"
    csv.write_text(
        "handle,platform,link_pgp\n"
        "kr4ken,github,ABCD1234ABCD1234ABCD1234ABCD1234ABCD1234\n"
        "kraken_x,telegram,ABCD1234ABCD1234ABCD1234ABCD1234ABCD1234\n")
    inv = Investigator(offline=True).investigate(f"same actor? {csv}")
    assert "HANDLE CORRELATION" in inv.report
    assert "handle-correlation" in inv.packages_used
    assert any("upper bounds" in w for w in inv.warnings)


# ---- planner selection ---------------------------------------------------- #

def test_falls_back_to_rules_without_an_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert Investigator(offline=True).planner.name == "rules"


def test_unknown_subject_explains_rather_than_guessing():
    inv = Investigator(offline=True).investigate("tell me something interesting")
    assert inv.conclusion == "could not identify a subject"
    assert "Try naming one explicitly" in inv.report


# ---- expansion wiring ------------------------------------------------------ #
#
# The orchestrator resolved a name and stopped. These assert the fan-out runs
# and that it degrades rather than crashing on partial dependencies.

def test_resolved_name_triggers_expansion():
    inv = Investigator(offline=True).investigate("who operates scraper-site.example?")
    assert inv.expansion is not None
    assert inv.expansion.total_discovered > 0
    assert "Expansion from" in inv.report


def test_expansion_surfaces_the_estate_from_one_payee_record():
    inv = Investigator(offline=True).investigate("who operates scraper-site.example?")
    d = inv.expansion.discovered
    assert d.get("seller accounts")
    assert d.get("authorising sites")


def test_expansion_warns_so_the_caller_notices():
    inv = Investigator(offline=True).investigate("who operates scraper-site.example?")
    assert any("further resource" in w for w in inv.warnings)


def test_expansion_degrades_on_an_index_without_reverse_lookup():
    """Any object can be passed as an index. Assuming the shape raised
    AttributeError mid-run and lost the whole investigation."""
    import asyncio

    from paytrace.enrich import Route, expand_from_name

    class Partial:
        def holders(self, ident):
            return 1

    exp = asyncio.run(expand_from_name("Example Media Ltd", index=Partial()))
    reasons = dict(exp.empty)
    assert Route.REVERSE_SELLERS in reasons
    assert "does not support reverse name lookup" in reasons[Route.REVERSE_SELLERS]


def test_expansion_accepts_both_fetcher_protocols():
    """Collectors use async get(); agent tools use sync get_text(). Both are
    real and neither can be assumed."""
    import asyncio

    from paytrace.enrich import _fetch_text

    class Sync:
        def get_text(self, url):
            return "<p>Operated by Example Media Ltd</p>"

    class Async:
        async def get(self, url, allow_html=False):
            class R:
                status = 200
                text = "<p>Operated by Example Media Ltd</p>"
            return R()

    for f in (Sync(), Async()):
        assert asyncio.run(_fetch_text(f, "https://x.example/terms"))


def test_expansion_reports_no_fetcher_rather_than_failing():
    import asyncio

    from paytrace.enrich import Route, expand_from_name

    exp = asyncio.run(expand_from_name("Example Media Ltd"))
    reasons = dict(exp.empty)
    assert "did not run" in reasons.get(Route.DOCUMENT_SEARCH, "")


# ---- the live path, not just --offline ------------------------------------ #

def _live_world(monkeypatch):
    """Run the real async Fetcher against an in-memory world. This is the path
    `attribution ask` takes without --offline; the fixture fetcher used by
    --offline never reaches the adapter that crashed."""
    import httpx
    import paytrace.net as net

    def handler(req):
        h, p = req.url.host, req.url.path
        if p == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if h == "live-site.example" and p == "/ads.txt":
            return httpx.Response(200, text="adnet.example, 7741, DIRECT\n")
        if h == "adnet.example" and p == "/sellers.json":
            return httpx.Response(200, json={"sellers": [{
                "seller_id": "7741", "name": "Example Co Ltd",
                "domain": "exampleco.example", "seller_type": "PUBLISHER"}]})
        if h == "live-site.example" and p == "/":
            return httpx.Response(200, text="<html>hi</html>",
                                  headers={"content-type": "text/html"})
        return httpx.Response(404)

    original = net.Fetcher.__init__

    def patched(self, *a, **k):
        k["resolver"] = lambda host: ["93.184.216.34"]
        original(self, *a, **k)
        for route in self.egress.egresses:
            self._clients[route.label] = httpx.AsyncClient(
                transport=httpx.MockTransport(handler), follow_redirects=False)

    monkeypatch.setattr(net.Fetcher, "__init__", patched)


def test_live_investigation_does_not_crash_at_the_pivot(monkeypatch, tmp_path):
    """Every live `attribution ask` crashed with "asyncio.run() cannot be
    called from a running event loop": the pivot step runs inside a loop and
    fell back to a sync adapter that starts its own. Only --offline had been
    tested, so it shipped in 2.0.1."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _live_world(monkeypatch)
    inv = Investigator(offline=False).investigate(
        "who operates live-site.example?", out=str(tmp_path / "out"))
    assert inv.pivot is not None, "the pivot step must run to completion"

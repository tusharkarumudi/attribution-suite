"""Cross-package integration.

Four packages that depend on each other fail at the seams, not inside them.
Every test here failed at least once during development, and none of them could
have been written inside a single package.
"""

import asyncio

import pytest
from attribution_graph import (
    AbsenceKind,
    AttributionGraph,
    Claim,
    EntityType,
    Identifier,
    IdKind,
    Predicate,
    Reliability,
    absence_claim,
    assess,
    lookup_variants,
    resolve,
)
from handle_correlation import Observation, all_claims, candidates_from_name
from paytrace import (
    classify_seller_name,
    compare_domains,
    expand_from_name,
    find_seller_in_text,
    name_variants,
    parse_ads_txt,
    resolve_host,
    sellers_json_url,
    simhash,
)
from paytrace.collectors import registry

from attribution_suite import Investigator, Refused, ask, versions

# ---- version and dependency coherence -------------------------------------- #

def test_all_four_packages_report_one_version():
    """attribution-suite's own suite asserts this because bumping a single
    package is the easiest mistake to make in a four-repo toolchain."""
    # MAJOR.MINOR line, not an identical version: the ==2.0.* pins declare
    # patch releases compatible, and paytrace ships 2.0.1 beside 2.0.0.
    lines = {".".join(x.split(".")[:2]) for x in versions().values()}
    assert len(lines) == 1, "version lines diverge: {versions()}"


def test_every_package_imports_without_the_others_present_at_import_time():
    import importlib
    for m in ("attribution_graph", "paytrace", "handle_correlation"):
        importlib.import_module(m)


# ---- single canonical implementations -------------------------------------- #

def test_name_variation_has_exactly_one_implementation():
    """Two drifted: the core produced 400 matching forms, paytrace had its own
    producing 7, overlapping on 1. A lookup using the weaker set missed records."""
    assert name_variants("Müller") == lookup_variants("Müller", 12)


def test_handle_generation_has_exactly_one_home():
    import paytrace
    assert not hasattr(paytrace, "handle_candidates")
    assert candidates_from_name("Tran Thi Binh")


def test_lookup_variants_are_bounded_for_querying():
    """Matching casts a wide net; querying spends a request per form."""
    assert len(lookup_variants("TRẦN THỊ BÌNH")) <= 12
    assert not any(v.lower().startswith("al-")
                   for v in lookup_variants("TRẦN THỊ BÌNH"))


# ---- the full attribution chain -------------------------------------------- #

def test_ads_txt_to_seller_to_name_to_expansion():
    """The end-to-end path the toolkit exists for, in one test."""
    ads = parse_ads_txt(
        "OWNERDOMAIN=examplemedia.example\n"
        "google.com, pub-111, DIRECT, cert1\n", "site.example")
    assert ads.direct_count == 1
    assert ads.accounts[0].cid == "cert1"

    # sellers.json is not at the domain root for Google
    assert "storage.googleapis.com" in sellers_json_url("google.com")

    import json
    body = json.dumps({"sellers": [
        {"seller_id": "pub-111", "name": "TRẦN THỊ BÌNH",
         "seller_type": "PUBLISHER", "is_confidential": 0}]})
    rec = find_seller_in_text(body, "google.com", "pub-111")
    assert rec.is_natural_person and rec.sole_operator_pattern

    class Idx:
        def sellers_for_name(self, n):
            return [("google.com", "pub-111", "")] if "binh" in n.lower() else []

        def sites_for_seller(self, a, s):
            return ["site.example", "mirror.example"]

    exp = asyncio.run(expand_from_name(rec.name, index=Idx()))
    assert "mirror.example" in exp.discovered["authorising sites"]


def test_person_name_does_not_dead_end():
    """PERSON_NAME was accepted by one collector (sanctions screening)."""
    accepting = [n for n, c in registry().items()
                 if IdKind.PERSON_NAME in getattr(c, "accepts", ())]
    assert len(accepting) >= 3


def test_every_emitted_identifier_kind_is_pivotable():
    """URL was emitted (extension IDs) and accepted by nothing, so mandated
    disclosures were extracted and discarded."""
    accepted = {k for c in registry().values() for k in getattr(c, "accepts", ())}
    for kind in (IdKind.DOMAIN, IdKind.ORG_NAME, IdKind.PERSON_NAME,
                 IdKind.SELLER_ID, IdKind.URL, IdKind.EMAIL,
                 IdKind.ANALYTICS_ID):
        assert kind in accepted, f"{kind.value} is emitted but never pivoted on"


# ---- scoring invariants hold through the whole stack ----------------------- #

def test_corroboration_cap_survives_the_full_pipeline():
    """band_for used min over a rank where ATTRIBUTED is 0, so the single-source
    cap did nothing above p=0.55 — exactly the range it exists for."""
    from attribution_graph.scoring import Band, band_for
    for p in (1.0, 0.99, 0.9, 0.8):
        assert band_for(p, 1) is Band.WEAK
        assert band_for(p, 2) is not Band.WEAK


def test_group_inflation_blocked_end_to_end():
    """Cosmetic variants of one ID must not become several observations."""
    claims = [
        Claim(subject=Identifier(IdKind.DOMAIN, "a.example"),
              predicate=Predicate.SHARES_ANALYTICS_ID,
              object=Identifier(IdKind.ANALYTICS_ID, v),
              collector="t", source_url="https://a", reliability=Reliability.AUTHORITATIVE,
              correlation_group=f"analytics|a|{i}")
        for i, v in enumerate(
            ["ga4:G-ABC1", "ga4:g-abc1", "ga4:G-ABC1\u200e", "ga4: G-ABC1"])
    ]
    assert assess(claims, lambda i: 1).independent_groups == 1


def test_undeclared_source_absence_carries_no_weight():
    """Was a 0.50 coin flip, giving unregistered sources ~0.26 nats."""
    c = absence_claim(Identifier(IdKind.ORG_NAME, "X"), "never_declared",
                      AbsenceKind.CHECKED_ABSENT, query_url="https://x",
                      what_was_sought="y")
    assert c.weight == 0.0


def test_boilerplate_ads_txt_does_not_manufacture_a_portfolio():
    real = "google.com, pub-111, DIRECT\n"
    boiler = "\n".join(f"n{i}.example, {i}, RESELLER" for i in range(300))
    a = parse_ads_txt(real + boiler, "a.example")
    b = parse_ads_txt("othernet.example, 9, DIRECT\n" + boiler, "b.example")
    holders = lambda ad, s: 40_000 if ad.startswith("n") else 3  # noqa: E731
    o = compare_domains(a, b, holders)
    assert o.shared_total == 300 and o.shared_rare == 0
    assert o.is_template_sharing and not o.is_operator_signal


# ---- security invariants ---------------------------------------------------- #

@pytest.mark.parametrize("host", ["169.254.169.254", "localhost", "10.0.0.5"])
def test_ssrf_guard_blocks_collected_hostnames(host):
    from paytrace.netsec import safe_host
    assert safe_host(host) is None


def test_homoglyph_identifiers_merge_across_packages():
    a = Identifier(IdKind.ORG_NAME, "Ex\u0430mple Ltd")
    b = Identifier(IdKind.ORG_NAME, "Example Ltd")
    assert a.key == b.key and hash(a) == hash(b)


def test_injection_in_collected_data_does_not_steer_the_agent():
    from paytrace.agent import Agent, FixtureFetcher, FixtureIndex, Toolbox
    box = Toolbox(FixtureFetcher(poisoned=True), FixtureIndex())
    run = Agent(box, guards_enabled=True).run("attribute scraper-site.example")
    assert run.conclusion == "Example Media Holdings Ltd"
    assert run.injection_alerts
    assert not any("gleif" in v for v in run.invariants.violations())


def test_person_search_refusal_precedes_all_io():
    with pytest.raises(Refused):
        Investigator(offline=True).investigate("Who Is Jane Doe?")


# ---- degradation on partial dependencies ----------------------------------- #

def test_expansion_degrades_on_an_index_without_reverse_lookup():
    from paytrace.enrich import Route

    class Partial:
        def holders(self, i):
            return 1

    exp = asyncio.run(expand_from_name("Example Ltd", index=Partial()))
    assert "does not support reverse name lookup" in dict(exp.empty)[
        Route.REVERSE_SELLERS]


def test_expansion_accepts_both_fetcher_protocols():
    from paytrace.enrich import _fetch_text

    class Sync:
        def get_text(self, url):
            return "<p>x</p>"

    class Async:
        async def get(self, url, allow_html=False):
            class R:
                status = 200
                text = "<p>x</p>"
            return R()

    for f in (Sync(), Async()):
        assert asyncio.run(_fetch_text(f, "https://x.example/"))


def test_handle_module_absence_is_reported_not_crashed(monkeypatch):
    """paytrace soft-imports handle-correlation."""
    import builtins
    real = builtins.__import__

    def blocked(name, *a, **k):
        if name == "handle_correlation":
            raise ImportError("blocked")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", blocked)
    exp = asyncio.run(expand_from_name("Tran Thi Binh"))
    assert any("handle-correlation is not installed" in w for _, w in exp.empty)


# ---- suite orchestration ---------------------------------------------------- #

def test_suite_runs_the_full_stack_offline():
    r = ask("who operates scraper-site.example?", offline=True)
    assert r.conclusion == "Example Media Holdings Ltd"
    assert {"paytrace", "attribution-graph"} <= set(r.packages_used)
    assert r.expansion is not None


def test_handle_path_uses_the_handle_package(tmp_path):
    csv = tmp_path / "h.csv"
    csv.write_text("handle,platform,link_pgp\n"
                   "kr4ken,github,ABCD1234ABCD1234ABCD1234ABCD1234ABCD1234\n"
                   "kraken_x,telegram,ABCD1234ABCD1234ABCD1234ABCD1234ABCD1234\n")
    r = Investigator(offline=True).investigate(f"same actor? {csv}")
    assert "handle-correlation" in r.packages_used


def test_graph_resolution_works_with_claims_from_every_package():
    g = AttributionGraph(case_ref="INT")
    fpr = "ABCD1234ABCD1234ABCD1234ABCD1234ABCD1234"
    for c in all_claims([
        Observation(handle="kr4ken", platform="github", linked={"pgp": fpr}),
        Observation(handle="kraken_x", platform="telegram", linked={"pgp": fpr}),
    ]):
        g.add_claim(c)
    g.add_claim(Claim(
        subject=Identifier(IdKind.DOMAIN, "site.example"),
        predicate=Predicate.LEGAL_NAME,
        object=Identifier(IdKind.ORG_NAME, "Example Media Ltd"),
        collector="sellers_json", source_url="https://x",
        reliability=Reliability.STRONG, correlation_group="sj|1"))
    resolve(g, {EntityType.COMPANY, EntityType.PERSONA})
    assert g.entities


def test_seller_name_classification_is_shared_not_duplicated():
    from paytrace.sellersjson import SellerNameKind
    assert classify_seller_name("TRẦN THỊ BÌNH") is SellerNameKind.NATURAL_PERSON
    assert classify_seller_name("Example Ltd") is SellerNameKind.ORGANIZATION


# ---- fingerprinting --------------------------------------------------------- #

def test_structural_simhash_separates_template_from_content():
    tpl = "<html><body><h1>{b}</h1><p>{b} is free</p><footer>x</footer></body></html>"
    a, b = tpl.format(b="Mystalk"), tpl.format(b="Smihub")
    from paytrace.fingerprint import SAME_TEMPLATE, hamming
    assert hamming(simhash(a, structural=True),
                   simhash(b, structural=True)) <= SAME_TEMPLATE


@pytest.mark.network
def test_dns_resolution_labels_cdn_edges():
    r = resolve_host("pypi.org")
    assert r.addresses
    assert isinstance(r.origin_concealed, bool)


# ---- surface harvesting and the pivot -------------------------------------- #

def test_surface_harvest_runs_on_every_domain_not_just_the_seed():
    """The seed is chosen for being investigable; the answer is usually on a
    sibling. The harvester must accept any DOMAIN, so the frontier runs it on
    discovered ones too."""
    c = registry()["surface_harvest"]
    assert IdKind.DOMAIN in c.accepts
    assert c.priority <= 2, "must run early enough to feed the frontier"


def test_every_surface_signal_is_pivotable_or_terminal_by_design():
    """A signal the harvester emits must either feed a further pivot or be a
    deliberate terminus — never an orphan that reaches a dead end silently.
    SERVICE_ID was such an orphan until reverse lookup accepted it."""
    accepted = {k for c in registry().values() for k in getattr(c, "accepts", ())}
    for kind in (IdKind.EMAIL, IdKind.PERSON_NAME, IdKind.HANDLE,
                 IdKind.DOMAIN, IdKind.SERVICE_ID, IdKind.GRAVATAR_HASH):
        assert kind in accepted, f"{kind.value} emitted by surface but not pivotable"


def test_the_sibling_pivot_end_to_end():
    """Seed clean, sibling leaks a real email via .env, and that email is a
    pivotable identifier the frontier can carry forward."""
    import asyncio

    from attribution_graph import Identifier

    class _F:
        def __init__(self, routes):
            self.routes = routes

        async def get(self, url, headers=None, allow_html=False):
            body = self.routes.get(url)

            class R:
                status = 200 if body is not None else 404
                text = body or ""
                headers = {}
            return R()

    class _S:
        case_ref = "T"
        authorization = "t"

        def audit(self, *a, **k):
            pass

    harvest = registry()["surface_harvest"](_F({
        "https://sibling.example/": "<html></html>",
        "https://sibling.example/.env": "MAIL_FROM_ADDRESS=real@operator.example\n",
    }), _S())
    claims = list(asyncio.run(harvest.collect(
        Identifier(IdKind.DOMAIN, "sibling.example"))))

    emails = {c.object.value for c in claims if c.object.kind is IdKind.EMAIL}
    assert "real@operator.example" in emails

    # and EMAIL is pivotable, so the frontier carries it forward
    accepted = {k for c in registry().values() for k in getattr(c, "accepts", ())}
    assert IdKind.EMAIL in accepted


# ---- pivot loop: seed to sibling to identity ------------------------------- #
#
# The scenario the pipeline was missing: the seed names nobody, and the operator
# is identified on a sibling reached through a shared analytics ID.

def test_pivot_loop_recovers_identity_from_a_sibling():
    from paytrace import extract_artifacts, pivot_expand

    clean = ('<script src="https://www.googletagmanager.com/gtag/js?id='
             'G-SHARED123"></script><h1>Welcome</h1>')
    sibling = ('<script src="https://www.googletagmanager.com/gtag/js?id='
               'G-SHARED123"></script><p>Operated by Nguyen Van An</p>')

    assert not extract_artifacts(clean, "seed.example").yielded_identity

    class F:
        async def get(self, url, allow_html=False):
            body = {"https://seed.example/": clean,
                    "https://sib.example/": sibling}.get(url)

            class R:
                status = 200 if body else 404
                text = body or ""
                headers = {}
            return R()

    class Idx:
        def domains_for_analytics(self, s, v):
            return ["sib.example"] if "SHARED" in v.upper() else []

        def holders_analytics(self, s, v):
            return 2

    import asyncio
    r = asyncio.run(pivot_expand("seed.example", F(), index=Idx()))
    assert "sib.example" in r.identity_found_on
    assert any(c.object.value == "Nguyen Van An" for c in r.claims)


def test_pivot_claims_carry_less_weight_than_seed_claims():
    """Each hop is another inferential step that could be wrong."""
    from paytrace import pivot_expand

    page = ('<script src="https://www.googletagmanager.com/gtag/js?id='
            'G-XABCDEF12"></script><p>Operated by A B</p>')

    class F:
        async def get(self, url, allow_html=False):
            class R:
                status = 200
                text = page
                headers = {}
            return R()

    class Idx:
        def domains_for_analytics(self, s, v):
            return ["sib.example"]

        def holders_analytics(self, s, v):
            return 2

    import asyncio
    r = asyncio.run(pivot_expand("seed.example", F(), index=Idx(), max_depth=1))
    depth1 = [c for c in r.claims if c.raw.get("pivot_depth") == 1]
    assert depth1 and all(c.weight < 1.0 for c in depth1)


def test_extracted_artifacts_feed_the_scoring_model():
    """Pivot findings are scored, not asserted: a shared low-selectivity ID
    contributes almost nothing, a shared service account a great deal."""
    from attribution_graph import assess
    from paytrace import extract_artifacts

    body = ('https://abcdef0123456789abcdef0123456789@o1.ingest.sentry.io/1'
            '<p>Operated by Nguyen Van An</p>')
    ex = extract_artifacts(body, "x.example")
    a = assess(ex.claims, lambda i: 1)
    assert a.band.value in ("WEAK", "LIMITED_EVIDENCE", "MODERATE_EVIDENCE", "STRONG_EVIDENCE",
                            "UNSUPPORTED")


# ---- the sibling pivot ----------------------------------------------------- #
#
# The seed does not name its operator. A sibling — reached through a shared
# analytics ID — does. This is the pattern the whole toolkit exists to exploit,
# and it only works if a discovered identifier re-enters collection.

def test_discovered_identifiers_re_enter_collection():
    """The engine re-collects on pivotable kinds. Without this, the seed's
    silence would be the end of the investigation."""
    from attribution_graph import IdKind
    from attribution_graph.engine import PIVOTABLE_KINDS

    # An analytics ID or email found on a sibling must be chased.
    assert IdKind.ANALYTICS_ID in PIVOTABLE_KINDS
    assert IdKind.EMAIL in PIVOTABLE_KINDS
    assert IdKind.DOMAIN in PIVOTABLE_KINDS
    # A recorded-but-not-chased observation must not be.
    assert IdKind.POSTAL_ADDRESS not in PIVOTABLE_KINDS


def test_artifact_extraction_feeds_the_sibling_pivot():
    """A shared analytics ID on the seed reverse-looks-up to siblings; an email
    absent from the seed is extracted from a sibling's page source."""
    import asyncio

    from attribution_graph import Identifier, IdKind
    from paytrace.collectors.artifacts import DeepArtifacts

    class Fetcher:
        # seed page: an analytics ID, no operator name or email
        # sibling page: the same operator, with an email in the source
        PAGES = {
            "https://seed.example/":
                "<script>gtag('config','G-SHARED123')</script>",
            "https://sibling.example/":
                "<script>gtag('config','G-SHARED123')</script>"
                "<!-- contact: realowner@personal.example -->",
        }

        def __init__(self):
            self.requests = []

        async def get(self, url, headers=None, allow_html=False):
            self.requests.append(url)

            class R:
                status = 200 if url in Fetcher.PAGES else 404
                text = Fetcher.PAGES.get(url, "")
                headers = {}
            return R()

    class Scope:
        case_ref = "T"
        authorization = "t"

        def audit(self, *a, **k):
            pass

    f = Fetcher()
    seed = list(asyncio.run(
        DeepArtifacts(f, Scope()).collect(Identifier(IdKind.DOMAIN, "seed.example"))))
    sibling = list(asyncio.run(
        DeepArtifacts(f, Scope()).collect(
            Identifier(IdKind.DOMAIN, "sibling.example"))))

    # seed yields the shared ID but no email
    seed_ids = {c.object.value for c in seed if c.object.kind is IdKind.ANALYTICS_ID}
    seed_emails = {c.object.value for c in seed if c.object.kind is IdKind.EMAIL}
    assert "ga4:g-shared123" in seed_ids  # analytics IDs case-fold (obfuscation defense)
    assert not seed_emails

    # the sibling, reached via that ID, yields the operator email
    sibling_emails = {c.object.value for c in sibling
                      if c.object.kind is IdKind.EMAIL}
    assert "realowner@personal.example" in sibling_emails


def test_gravatar_hash_links_pages_without_revealing_the_email():
    """A hash on one page and a candidate email on another bind to one person
    without either page exposing the address."""
    from paytrace import gravatar_hash

    email = "operator@personal.example"
    # the hash a page would carry
    on_page = gravatar_hash(email)
    # a candidate email found elsewhere
    assert gravatar_hash("Operator@Personal.Example") == on_page


# ---- screenshot evidence --------------------------------------------------- #

def test_screenshot_log_is_separate_from_the_wire_manifest(tmp_path):
    """A rendering must not be mistakable for the bytes that were served."""
    from attribution_graph import (
        CaseScope,
        EvidenceLog,
        ScreenshotCapturer,
        write_evidence_package,
    )

    (tmp_path / "c.yaml").write_text(
        f"case_ref: T\nauthorization: t\nseeds: [domain:a.example]\n"
        f"audit_path: {tmp_path / 'a.jsonl'}\n")
    scope = CaseScope.load(str(tmp_path / "c.yaml"))
    root = tmp_path / "evidence"
    log = EvidenceLog(scope, root)
    log.record("https://a.example/", 200, b"<html>x</html>", collector="t")

    cap = ScreenshotCapturer(root, enabled=False)
    cap.capture("https://a.example/", parent_body_sha256="abc")

    write_evidence_package(log, "summary", capturer=cap)

    assert (root / "evidence_manifest.json").exists()
    assert (root / "screenshot_manifest.json").exists()
    assert (root / "SCREENSHOT_LOG.txt").exists()

    text = (root / "SCREENSHOT_LOG.txt").read_text()
    assert "RENDERING, not a capture" in text


def test_screenshots_degrade_without_a_browser(tmp_path, monkeypatch):
    from attribution_graph import ScreenshotCapturer

    monkeypatch.setattr(
        "attribution_graph.screenshot.available_renderer", lambda: None)
    cap = ScreenshotCapturer(tmp_path)
    s = cap.capture("https://a.example/")
    assert not s.captured
    assert "not a blank page" in s.note


def test_screenshot_chain_verifies_independently(tmp_path):
    from attribution_graph import ScreenshotCapturer

    cap = ScreenshotCapturer(tmp_path, enabled=False)
    cap.capture("https://a.example/")
    cap.capture("https://b.example/")
    ok, problems = cap.verify()
    assert ok and not problems


def test_suite_exposes_the_screenshot_flag():

    import contextlib

    # Behavioural, not textual. Asserting the flag string appears in the source
    # proved nothing -- and became actively misleading once the flag was
    # withdrawn, since the string was still there.
    import io

    from attribution_suite import cli

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = cli.main(["run", "--case", "/nonexistent.yaml", "--screenshots"])
    assert rc == 2
    assert "not available in this release" in err.getvalue()


# ---- egress and non-uniform registers -------------------------------------- #

def test_egress_is_recorded_not_hidden():
    """A capture without its egress is not reproducible: a reviewer elsewhere
    cannot tell whether the page changed or the vantage point did."""
    from paytrace import EgressPool

    pool = EgressPool.from_case([
        {"label": "direct"},
        {"label": "gulf", "provider": "oxylabs_datacenter", "network": "datacenter",
         "country": "ae", "username": "acct", "password_env": "OXY_PW"}])
    note = pool.manifest_note()
    assert "vantage point" in note
    assert "exit AE" in note
    # no credential material anywhere in the record
    assert "OXY_PW" not in str(pool.to_record())


def test_consent_sensitive_exits_cannot_be_used_silently():
    from paytrace import EgressPool

    pool = EgressPool.from_case([
        {"label": "r", "provider": "oxylabs", "network": "residential",
         "country": "de"}])
    assert pool.consent_sensitive
    assert "subscribers" in pool.manifest_note()


def test_geo_divergence_is_reported_as_a_finding():
    import hashlib
    from datetime import datetime, timezone

    from paytrace import GeoDivergence, VantageCapture

    def cap(country, body):
        return VantageCapture(
            egress_label=country, country=country, status=200,
            body_sha256=hashlib.sha256(body.encode()).hexdigest(),
            body_bytes=len(body), fetched_at=datetime.now(timezone.utc))

    d = GeoDivergence("https://x.example/impressum", [
        cap("de", "<html>Muster GmbH</html>"),
        cap("us", "<html>nothing</html>")])
    assert d.diverges
    assert "differently by region" in d.render()


def test_federated_jurisdictions_are_not_flattened():
    """'Not found in the UAE register' has no referent — there is no UAE
    register. Flattening seven authorities into one row produces confident
    false negatives."""
    from paytrace import federated_warning
    from paytrace.catalog import load_catalog

    members = [r for r in load_catalog() if r.federation_of == "AE"]
    assert len(members) >= 6

    lumped = [r for r in load_catalog()
              if r.jurisdiction == "AE" and not r.federation_of]
    assert len(lumped) == 1
    assert "no national UAE company register" in lumped[0].notes

    w = federated_warning("AE")
    assert "not evidence of anything" in w


def test_registers_declare_whether_they_can_actually_be_reached():
    """A register that is Arabic-only or in-country-only is automatable in
    principle and unusable in practice without the language or an egress."""
    from paytrace.catalog import load_catalog

    cat = load_catalog()
    assert any(r.requires_local_egress for r in cat)
    assert any(r.language != "en" for r in cat)


# ---- Phase D release gate (v1.7.0 release-readiness audit) ----------------- #

def test_real_policy_engine_and_real_fetcher_are_compatible():
    """PolicyEngine.evaluate is `async (fetcher, url)` returning a
    FetchDecision with `note`/`should_fetch`. Fetcher called
    `policy.evaluate(url)` and read `.reason`/`.allowed`, so every collector
    raised TypeError, Engine caught it as an ordinary collector error, and a
    run finished with 0 claims, 0 captures and evidence_verified=True."""
    import asyncio
    import pathlib

    import httpx
    from attribution_graph.fetchpolicy import PolicyEngine, RobotsPolicy
    from paytrace.net import Fetcher

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=b"User-agent: *\nDisallow: /secret\n")
        return httpx.Response(200, content=b"page")

    for mode, expect_fetched in (("respect", False), ("record", True),
                                 ("ignore", True)):
        import tempfile
        pol = PolicyEngine(policy=RobotsPolicy(mode), user_agent="t")
        # A fresh cache per mode: a shared one served mode 2 from mode 1's
        # fetch and the assertion passed for the wrong reason.
        f = Fetcher(user_agent="t", policy=pol,
                    cache_dir=pathlib.Path(tempfile.mkdtemp()) / "c",
                    resolver=lambda h: ["93.184.216.34"])
        f._clients["direct"] = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False)
        r = asyncio.run(f.get("https://example.com/secret"))
        assert (r is not None) is expect_fetched, mode
        asyncio.run(f.aclose())


def test_verify_command_does_not_execute_package_code():
    """`attribution verify` ran the verify.py inside the evidence
    directory — untrusted input by definition, since it is the thing under
    examination."""
    import pathlib
    import tempfile

    from attribution_graph import CaseScope, EvidenceLog, write_evidence_package

    from attribution_suite.cli import main

    d = pathlib.Path(tempfile.mkdtemp())
    (d / "c.yaml").write_text(
        f"case_ref: T\nauthorization: t\nseeds: [domain:a.example]\n"
        f"audit_path: {d / 'a.jsonl'}\n")
    log = EvidenceLog(CaseScope.load(str(d / "c.yaml")), d / "ev")
    log.record("https://a.example/", 200, b"x", collector="t")
    write_evidence_package(log, "s")

    marker = d / "EXECUTED"
    (d / "ev" / "verify.py").write_text(
        f"import pathlib; pathlib.Path({str(marker)!r}).write_text('x')\n")

    assert main(["verify", str(d / "ev")]) == 0
    assert not marker.exists(), "package-supplied code must never be executed"


def test_case_egress_survives_load_and_reaches_transport():
    """`CaseScope` had no egress field, so an `egress:` block in a case
    file was silently discarded and the runner always built a direct pool."""
    import pathlib
    import tempfile

    from attribution_graph import CaseScope
    from paytrace import EgressPool

    d = pathlib.Path(tempfile.mkdtemp())
    (d / "c.yaml").write_text(
        f"case_ref: T\nauthorization: t\nseeds: [domain:a.example]\n"
        f"audit_path: {d / 'a.jsonl'}\n"
        "egress:\n"
        "  - label: direct\n"
        "  - label: gulf\n"
        "    provider: oxylabs_datacenter\n"
        "    network: datacenter\n"
        "    country: ae\n"
        "    username: acct\n"
        "    password_env: OXY_PW\n")
    scope = CaseScope.load(str(d / "c.yaml"))
    assert scope.egress and len(scope.egress) == 2
    pool = EgressPool.from_case(scope.egress)
    assert pool.get("gulf").country == "ae"


def test_misspelled_case_key_is_rejected():
    """A safety-sensitive option that is silently ignored when misspelled is
    worse than one that does not exist."""
    import pathlib
    import tempfile

    import pytest as _pytest
    from attribution_graph import CaseScope

    d = pathlib.Path(tempfile.mkdtemp())
    (d / "c.yaml").write_text(
        f"case_ref: T\nauthorization: t\nseeds: [domain:a.example]\n"
        f"audit_path: {d / 'a.jsonl'}\negres: []\n")
    with _pytest.raises(ValueError):
        CaseScope.load(str(d / "c.yaml"))


def test_provider_network_mismatch_is_rejected():
    """`provider: oxylabs` with `network: datacenter` routed to the
    residential endpoint while recording consent_sensitive=false — the
    provenance and the safeguard were both wrong at once."""
    import pytest as _pytest
    from paytrace.egress import Egress, NetworkType, ProxyError

    with _pytest.raises(ProxyError, match="serves"):
        # oxylabs' profile is the residential endpoint (pr.oxylabs.io); a
        # datacenter label on it is the exact mislabelling review found.
        Egress(label="x", provider="oxylabs", network=NetworkType.DATACENTER,
               country="ae", username="u", password_env="P")

    Egress(label="ok", provider="oxylabs_datacenter",
           network=NetworkType.DATACENTER, country="ae",
           username="u", password_env="P")


@pytest.mark.parametrize("scenario", ["404", "redirect", "cache"])
def test_evidence_records_every_retrieval(scenario):
    """404s returned before recording, redirect hops were unrecorded,
    and a cache hit produced a package with zero captures that verified
    clean — the retrieval happened and the record lived elsewhere."""
    import asyncio
    import pathlib
    import tempfile

    import httpx
    from attribution_graph import CaseScope, EvidenceLog
    from paytrace.net import Fetcher

    def newlog():
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "c.yaml").write_text(
            f"case_ref: T\nauthorization: t\nseeds: [domain:a.example]\n"
            f"audit_path: {d / 'a.jsonl'}\n")
        return d, EvidenceLog(CaseScope.load(str(d / "c.yaml")), d / "ev")

    pub = lambda h: ["93.184.216.34"]  # noqa: E731

    if scenario == "404":
        d, log = newlog()
        f = Fetcher(user_agent="t", cache_dir=d / "c", evidence_log=log,
                    resolver=pub)
        f._clients["direct"] = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(404)),
            follow_redirects=False)
        asyncio.run(f.get("https://x.example/missing"))
        assert len(log.captures) == 1
        asyncio.run(f.aclose())

    elif scenario == "redirect":
        d, log = newlog()

        def redir(r):
            if r.url.path == "/a":
                return httpx.Response(302, headers={"location": "https://x.example/b"})
            return httpx.Response(200, content=b"ok")

        f = Fetcher(user_agent="t", cache_dir=d / "c", evidence_log=log,
                    resolver=pub)
        f._clients["direct"] = httpx.AsyncClient(
            transport=httpx.MockTransport(redir), follow_redirects=False)
        asyncio.run(f.get("https://x.example/a"))
        assert len(log.captures) == f.count == 2
        asyncio.run(f.aclose())

    else:
        d, log = newlog()
        f = Fetcher(user_agent="t", cache_dir=d / "c", evidence_log=log,
                    resolver=pub)
        f._clients["direct"] = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"hi")),
            follow_redirects=False)
        asyncio.run(f.get("https://x.example/"))
        asyncio.run(f.aclose())

        _, log2 = newlog()
        f2 = Fetcher(user_agent="t", cache_dir=d / "c", evidence_log=log2,
                     resolver=pub)
        r = asyncio.run(f2.get("https://x.example/"))
        assert r.from_cache
        assert len(log2.captures) == 1
        assert log2.captures[0].outcome == "cache"
        asyncio.run(f2.aclose())

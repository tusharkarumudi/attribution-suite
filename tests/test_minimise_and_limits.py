"""Regressions for the minimisation contract, portfolio limits and metadata claims.

Three of these were introduced here: VERIFY.sh could report success for a killed
pytest, the clean-archive bootstrap broke PUSH.sh's identity handling, and the
minimise contract had been patched three times without ever being written down.
The tests are written against the contract now, not against the reported
instances.
"""

import re
from pathlib import Path
from urllib.parse import quote, quote_plus

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _survives(form: str, text: str) -> bool:
    """Does `form` appear in `text` as its own token?

    This is the contract minimisation actually implements: `minimise.scrub`
    replaces values at token boundaries, using this same pattern. Checking for
    a bare substring instead asserts something no hashing scheme can deliver.
    `abc` is three hex characters, so a random 32-hex claim id contains it
    0.78% of the time -- about one run in eleven across the dozen ids in these
    artifacts -- and this test failed on a build whose minimisation was
    perfectly correct, pointing at `e5020df` + `abc` + `32471cb30f6ef7c32b098a`.

    Loosening an assertion is only safe with a control that proves it still
    fires, which is `test_the_leak_check_still_catches_a_real_leak` below.
    """
    return re.search(rf"(?<![0-9A-Za-z]){re.escape(form)}(?![0-9A-Za-z])",
                     text) is not None


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


# ---- the minimise contract ---------------------------------------------- #

@pytest.mark.parametrize("kind,value", [
    ("person_name", "Jane Doe"),   # multi-word: encoding variants matter
    ("handle", "abc"),             # 3 chars: was exempted by len(v) > 3
    ("domain", "a.io"),            # short domain
])
def test_derived_artifacts_contain_no_form_of_a_seed(tmp_path, kind, value):
    """The contract: every identifier the toolkit derives is hashed in every
    artifact it generates, in every form it produces.

    Three separate violations were reported -- percent-encoding surviving,
    form-encoding surviving, and values of three characters or fewer exempted
    outright. All three came from export and audit each keeping their own list
    of forms."""
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
        f"case_ref: T\nauthorization: t\nseeds: [{kind}:{value}]\n"
        f"audit_path: {tmp_path / 'a.jsonl'}\nminimize: true\n"
        "salt: 00112233445566778899aabbccddeeff\n")
    scope = CaseScope.load(str(tmp_path / "c.yaml"))

    # exercise review path with the encodings our own collectors emit
    scope.audit("collect", url=f"https://x.example/search?q={quote(value)}",
                note=f"candidate {quote_plus(value)}")

    g = AttributionGraph(case_ref="T")
    for group in ("analytics", "ads_txt"):
        for who, k in ((value, getattr(IdKind, kind.upper())),
                       ("other-site.example", IdKind.DOMAIN)):
            g.add_claim(Claim(
                subject=Identifier(k, who),
                predicate=Predicate.SHARES_ANALYTICS_ID,
                object=Identifier(IdKind.ANALYTICS_ID, f"ga4:G-{group}"),
                collector="c", source_url=f"https://x.example/?q={quote(who)}",
                reliability=Reliability.AUTHORITATIVE,
                correlation_group=f"{group}|{who}"))

    out = tmp_path / "out"
    write_all(g, resolve(g, {EntityType.PERSON, EntityType.PERSONA,
                             EntityType.COMPANY}), scope, out)

    from attribution_graph.minimise import canonical_forms

    audit = (tmp_path / "a.jsonl").read_text()
    for form in canonical_forms(value):
        for artifact in out.iterdir():
            assert not _survives(form, artifact.read_text(errors="ignore")), \
                f"{form!r} survives in {artifact.name}"
        assert not _survives(form, audit), f"{form!r} survives in review log"


@pytest.mark.parametrize("leak", [
    '"subject": "handle:abc"',          # unhashed in a field
    "https://x.example/?q=abc",         # unhashed in a URL
    '"correlation_group": "analytics|abc"',
    "candidate abc, seen twice",        # prose in the review log
    '{"handle": "abc"}',
])
def test_the_leak_check_still_catches_a_real_leak(leak):
    """The control for `_survives`.

    A boundary-aware search is weaker than a substring search, so it has to be
    shown to still fire on every shape a genuine leak takes. If this test ever
    passes while reporting no leak, the check above has stopped checking.
    """
    assert _survives("abc", leak), f"a real leak went undetected in {leak!r}"


def test_a_random_id_that_contains_the_seed_is_not_a_leak(tmp_path, monkeypatch):
    """The regression for the flake itself, made deterministic.

    Claim and entity ids are `uuid4().hex`. CI drew
    `e5020dfabc32471cb30f6ef7c32b098a` for a case seeded with `abc` and failed
    a correct build. Forcing that exact id proves the check tolerates it --
    and a bare substring search, asserted here, does not.
    """
    import uuid

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
    from attribution_graph.minimise import canonical_forms

    colliding = "e5020dfabc32471cb30f6ef7c32b098a"
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(hex=colliding))

    (tmp_path / "c.yaml").write_text(
        "case_ref: T\nauthorization: t\nseeds: [handle:abc]\n"
        f"audit_path: {tmp_path / 'a.jsonl'}\nminimize: true\n"
        "salt: 00112233445566778899aabbccddeeff\n")
    scope = CaseScope.load(str(tmp_path / "c.yaml"))

    g = AttributionGraph(case_ref="T")
    for group in ("analytics", "ads_txt"):
        for who, k in (("abc", IdKind.HANDLE),
                       ("other-site.example", IdKind.DOMAIN)):
            g.add_claim(Claim(
                subject=Identifier(k, who),
                predicate=Predicate.SHARES_ANALYTICS_ID,
                object=Identifier(IdKind.ANALYTICS_ID, f"ga4:G-{group}"),
                collector="c", source_url=f"https://x.example/?q={who}",
                reliability=Reliability.AUTHORITATIVE,
                correlation_group=f"{group}|{who}"))

    out = tmp_path / "out"
    write_all(g, resolve(g, {EntityType.PERSON, EntityType.PERSONA,
                             EntityType.COMPANY}), scope, out)

    graph_json = (out / "investigation_graph.json").read_text()
    assert colliding in graph_json, "the colliding id was not used; test is void"
    assert "abc" in graph_json, "no substring collision; test is void"

    for form in canonical_forms("abc"):
        for artifact in out.iterdir():
            assert not _survives(form, artifact.read_text(errors="ignore")), \
                f"{form!r} survives in {artifact.name}"


def test_no_length_threshold_exempts_an_identifier():
    """`len(value) > 3` silently exempted short identifiers. A three-character
    handle is not less sensitive than a four-character one."""
    from attribution_graph.minimise import MIN_SCRUB_LENGTH, canonical_forms

    assert MIN_SCRUB_LENGTH == 0
    assert canonical_forms("abc"), "short values must still produce forms"


def test_export_and_audit_share_one_canonical_form_generator():
    """They drifted precisely because each had its own list."""
    export = (_repo("attribution-graph") / "src/attribution_graph/export.py").read_text()
    scope = (_repo("attribution-graph") / "src/attribution_graph/scope.py").read_text()
    assert "from .minimise import scrub" in export
    assert "from .minimise import canonical_forms" in scope


def test_documentation_states_evidence_is_not_minimised():
    """The docs claimed "no raw copy alongside" while preserved evidence keeps
    the raw bytes by design. Over-claiming a security property is worse than
    the gap it conceals."""
    guide = (_repo("attribution-suite") / "DEPLOYMENT.md").read_text()
    assert "not made safe to share" in guide
    assert "captures" in guide

    sec = (_repo("attribution-graph") / "SECURITY.md").read_text()
    assert "out of scope" in sec.lower()
    assert "evidence_manifest.json" in sec


# ---- the portfolio cap --------------------------------------------------- #

@pytest.mark.parametrize("cap", [1, 3, 5])
def test_ownerdomain_expansion_respects_max_domains(cap):
    """The cap was re-implemented per pivot and OWNERDOMAIN was not one of them,
    so a seed with ten self-asserted sites returned 11 domains under
    max_domains=3. Self-assertion is the surface an operator controls outright,
    which made the cheapest expansion the only uncapped one."""
    import asyncio

    from paytrace.portfolio import PortfolioExpander

    class Index:
        def universe(self): return 1_000_000
        def holders_analytics(self, *a): return 1
        def sellers_for_domain(self, d): return []
        def domains_for_analytics(self, *a): return []
        def sites_for_seller(self, *a): return []
        def owner_domains_for(self, d): return ["owner.example"]
        def sites_for_owner(self, o):
            return [f"site{i}.example" for i in range(10)]

    class E(PortfolioExpander):
        def __init__(self): self.index = Index()
        async def _ids_for_domain(self, d): return []

    result = asyncio.run(E().expand("seed.example", max_domains=cap))
    assert len(result.domains) <= cap


def test_portfolio_rejects_non_positive_max_domains():
    import asyncio

    from paytrace.portfolio import PortfolioExpander

    class E(PortfolioExpander):
        def __init__(self): self.index = None
        async def _ids_for_domain(self, d): return []

    with pytest.raises(ValueError, match="at least 1"):
        asyncio.run(E().expand("seed.example", max_domains=0))


def test_every_portfolio_addition_goes_through_the_gate():
    """The pattern, not the instance: three call sites each re-checked the cap
    and one forgot."""
    src = (_repo("paytrace") / "src/paytrace/portfolio.py").read_text()
    assert "_add_domain" in src
    body = src.split("async def expand(")[1]
    assert "res.domains.setdefault(" not in body, \
        "additions must route through _add_domain"


# ---- the gate cannot false-green ---------------------------------------- #



# ---- git identity ------------------------------------------------------- #



# ---- checksums outside the upload directory ----------------------------- #

@pytest.mark.parametrize("pkg", PKGS)
def test_checksums_are_not_written_into_dist(pkg):
    """`twine upload <dir>/*` passes everything in dist/ to twine, and upstream
    treats non-distribution files there as unsupported."""
    doc = yaml.safe_load(_wf(pkg, "release.yml").read_text())
    runs = " ".join(str(s.get("run", "")) for s in doc["jobs"]["release"]["steps"])
    assert "dist/SHA256SUMS" not in runs
    assert not re.search(r"\bcd dist\b", runs)
    assert "sha256sum dist/* > SHA256SUMS" in runs


@pytest.mark.parametrize("pkg", PKGS)
def test_github_release_still_publishes_the_checksums(pkg):
    # The release is created with `gh release create` now: the third-party
    # action was dropped because its own nested references are not SHA-pinned,
    # which the repository policy refuses. So the assets live in a shell array
    # rather than a `with: files:` block.
    doc = yaml.safe_load(_wf(pkg, "release.yml").read_text())
    runs = " ".join(str(s.get("run", "")) for s in doc["jobs"]["release"]["steps"])
    assert "gh release create" in runs
    assert "SHA256SUMS" in runs


# ---- publication metadata --------------------------------------------------- #

@pytest.mark.parametrize("pkg", PKGS)
def test_citation_version_matches_the_package(pkg):
    cff = (_repo(pkg) / "CITATION.cff").read_text()
    version = re.search(r"^version: (.+)$", cff, re.M).group(1).strip()
    pyproject = (_repo(pkg) / "pyproject.toml").read_text()
    assert version == re.search(r'^version = "(.+)"$', pyproject, re.M).group(1)


@pytest.mark.parametrize("pkg", PKGS)
def test_citation_does_not_claim_calibration(pkg):
    """The titles said "calibrated entity resolution" and "calibrated
    same-actor scoring", directly contradicting the methodology. A citation is
    the claim that outlives the repository."""
    assert "calibrated" not in (_repo(pkg) / "CITATION.cff").read_text().lower()


@pytest.mark.parametrize("pkg", PKGS)
def test_user_agent_reports_the_real_version(pkg):
    """Several reported 0.1, 0.2, 0.11 -- a site operator reading their logs
    would see a version that no longer exists."""
    version = re.search(r'^version = "(.+)"$',
                        (_repo(pkg) / "pyproject.toml").read_text(), re.M).group(1)
    for path in (_repo(pkg) / "src").rglob("*.py"):
        for match in re.finditer(
                r"(paytrace|attribution-suite|attribution-graph|handle-correlation)/([0-9][0-9.]*)",
                path.read_text()):
            assert match.group(2) == version, \
                f"{path.name}: User-Agent says {match.group(0)}"





@pytest.mark.parametrize("pkg", PKGS)
def test_no_user_agent_hardcodes_a_version(pkg):
    """Every User-Agent must derive its version. The earlier check listed the
    four package names, so `attribution-mcp/2.0.0` -- a fifth name -- kept
    reporting a withdrawn version after the bump to 2.0.1."""
    literal = re.compile(r"""user_agent\s*=\s*(f?)(["'])([^"']*)\2""")
    for path in (_repo(pkg) / "src").rglob("*.py"):
        for m in literal.finditer(path.read_text()):
            text = m.group(3)
            hard = re.search(r"/\d+\.\d+\.\d+", text)
            assert not hard, f"{path.name}: user_agent hardcodes {hard.group(0)!r}"

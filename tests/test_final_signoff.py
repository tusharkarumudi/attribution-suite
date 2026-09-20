"""Regressions for the final sign-off blockers, plus the metadata claims.

Three of these were self-inflicted: VERIFY.sh could report success for a killed
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


# ---- B1: the minimise contract ---------------------------------------------- #

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

    # exercise the audit path with the encodings our own collectors emit
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
            assert form not in artifact.read_text(errors="ignore"), \
                f"{form!r} survives in {artifact.name}"
        assert form not in audit, f"{form!r} survives in the audit log"


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


# ---- B2: the portfolio cap --------------------------------------------------- #

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


# ---- B3: the gate cannot false-green ---------------------------------------- #

def test_verify_classifies_on_exit_status_not_output():
    """A pytest killed with 137 whose last line was "." was classified PASS.
    A gate that can report success for a process that died is worse than none."""
    script = (_repo("attribution-suite") / "VERIFY.sh").read_text()
    assert "*failed*|*error*) fail" not in script
    assert "pytest exited $rc" in script


# ---- B4: git identity ------------------------------------------------------- #

def test_push_always_applies_the_supplied_git_identity():
    """Self-inflicted: VERIFY.sh now creates .git for its hygiene checks, so
    gating identity on `if [ ! -d .git ]` silently ignored GIT_EMAIL/GIT_NAME --
    failing on a machine with no global identity, and committing under the
    wrong one on a machine that has it."""
    script = (_repo("attribution-suite") / "PUSH.sh").read_text()
    init = script.index("git init -q -b main")
    email = script.index('git config user.email "$GIT_EMAIL"')
    assert email > init
    between = script[init:email]
    assert "fi" in between, "config must sit outside the `if [ ! -d .git ]` block"


# ---- B5: checksums outside the upload directory ----------------------------- #

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


def test_publishing_guide_is_consistent_about_deletion():
    """It said deleting "frees the version number for reuse" two lines before
    correctly saying the version cannot be reused."""
    guide = (_repo("attribution-suite") / "PUBLISHING.md").read_text()
    assert "frees the version number for reuse" not in guide
    assert "permanently reserves" in guide

"""Unified run: collect, score, resolve, verify, report.

One case file drives everything. The sequence is fixed because the ordering
matters: adversarial checks must run before scoring (a planted identifier must
not reach the model at full weight), and evidence verification must run before
reporting (findings from a package that failed its integrity check should never
be presented).
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from attribution_graph import (
    AttributionGraph,
    CaseScope,
    CompositeIndex,
    Engine,
    EvidenceLog,
    InMemoryIndex,
    PolicyEngine,
    ResolutionResult,
    RobotsPolicy,
    StepKind,
    Trail,
    load_blacklist,
    write_all,
    write_evidence_package,
)
from attribution_graph.evidence import secure_mkdir

# User-Agents derive from the package version so a bump can never
# leave a stale string behind. A patch bump previously left five
# User-Agents reporting the old version.
from ._version import __version__ as _VERSION  # noqa: E402


@dataclass
class SuiteResult:
    scope: CaseScope
    graph: AttributionGraph
    resolution: ResolutionResult | None
    trail: Trail
    evidence: EvidenceLog | None
    outputs: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        L = [f"case {self.scope.case_ref}",
             f"  {len(self.graph.identifiers)} identifiers, "
             f"{len(self.graph.claims)} claims, {len(self.graph.entities)} entities"]
        for k, v in self.stats.items():
            L.append(f"  {k}: {v}")
        if self.warnings:
            # De-duplicated, in order: the blocked report is appended more than
            # once, which printed the whole list twice.
            L.append("  warnings:")
            L += [f"    - {w}" for w in dict.fromkeys(self.warnings)]
        return "\n".join(L)


#: How many identifiers one run will verify. Each costs up to three
#: retrievals -- the live page, an archive CDX query and the ad system's
#: sellers.json -- so an unbounded pass would quietly spend a request budget
#: on verification instead of collection.
ADVERSARIAL_MAX = 12


#: Variables that mean "this machine's only way out is a proxy".
_PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy",
               "HTTP_PROXY", "http_proxy")


def _ambient_proxy() -> str:
    """The proxy this environment sets, if any. The value is never recorded."""
    import os

    return next((v for v in _PROXY_VARS if os.environ.get(v, "").strip()), "")


def _egress_pool(scope, egress_env: bool, warnings: list[str], *,
                 direct: bool = False):
    """Build the egress pool, adopting this environment's proxy when it is the
    only route out.

    `trust_env=False` on the HTTP client is not negotiable: a proxy picked up
    silently would make the evidence manifest record `direct` while the traffic
    left through someone else's exit, which is a fabricated chain of custody.
    But the invariant that protects is *the manifest must not claim a vantage
    point the traffic did not use* -- and refusing to use the proxy at all was
    a stricter rule than that invariant needs.

    So an ambient proxy is now adopted when nothing is declared, and recorded
    as the `environment` provider, which is what it is. The chain of custody
    stays truthful and the run works. Refusing instead produced the single most
    common failure anyone hit: inside a sandbox -- an assistant running the
    skill, a hosted notebook, a CI container -- every retrieval was blocked,
    the report read as though the sites had refused, and the remedy was a flag
    nobody knew to pass.

    `--direct` keeps the old behaviour for the one case that wants it: testing
    whether hosts are reachable from this machine's own vantage point, where
    adopting a proxy would answer a different question.
    """
    from paytrace import EgressPool

    declared = getattr(scope, "egress", None)
    if egress_env:
        if declared:
            warnings.append(
                "--egress-env overrides the egress declared in the case file")
        return EgressPool.from_case(
            [{"label": "environment", "provider": "environment"}],
            default="environment")

    var = _ambient_proxy()
    if var and not declared and not direct:
        warnings.append(
            f"egress: this environment sets {var} and nothing was declared, so "
            "the run routed through that proxy and recorded the vantage point "
            "as `environment` rather than `direct`. Captures were taken from "
            "the proxy's exit, not from this machine. Pass --direct to refuse "
            "it, or declare an egress in the case file to pin one.")
        return EgressPool.from_case(
            [{"label": "environment", "provider": "environment"}],
            default="environment")

    pool = EgressPool.from_case(declared)
    if var and declared:
        warnings.append(
            f"{var} is set in this environment but the case file declares its "
            "own egress, which wins. The declared exit is the one recorded.")
    elif var and direct:
        warnings.append(
            f"--direct: {var} is set but was not used, so retrievals leave "
            "from this machine. Hosts that do not resolve here will be "
            "reported as unreachable, which is the point of --direct and is "
            "not a statement about those sites.")
    return pool


def _seed_domain(scope) -> str:
    """The domain this case is about, if it is about one."""
    for s in scope.seeds:
        if s.startswith("domain:"):
            return s.split(":", 1)[1]
        if ":" not in s:
            return s
    return ""


def _assessable_identifiers(graph, scope, index, *, limit: int):
    """Identifiers worth verifying, as (key, scheme, value, adsystem, id, sharing).

    Only identifiers that support a claim about the seed. Verifying every
    identifier in the graph would spend the budget proving things about third
    parties, which is the same mistake the findings output used to make.
    """
    from attribution_graph import IdKind

    seed = _seed_domain(scope)
    out, seen = [], set()
    for c in graph.claims:
        sides = [s for s in (c.subject, c.object) if getattr(s, "kind", None)]
        if seed and not any(s.kind is IdKind.DOMAIN and s.value == seed
                            for s in sides):
            continue
        for s in sides:
            if s.value in seen:
                continue
            if s.kind is IdKind.ANALYTICS_ID and ":" in s.value:
                scheme, value = s.value.split(":", 1)
                sharing = []
                if index is not None:
                    with contextlib.suppress(Exception):
                        sharing = index.domains_for_analytics(scheme, value)
                seen.add(s.value)
                out.append((s.value, scheme, value, None, None, sharing))
            elif s.kind is IdKind.SELLER_ID and "/" in s.value:
                adsys, sid = s.value.split("/", 1)
                seen.add(s.value)
                out.append((s.value, "seller", sid, adsys, sid, []))
        if len(out) >= limit:
            break
    return out[:limit]


def run_case(
    case_path: str | Path,
    outdir: str | Path = "./out",
    *,
    corpus_index: str | Path | None = None,
    blacklist: str | Path | None = None,
    handles: str | Path | None = None,
    spiderfoot: str | Path | None = None,
    opencti: str | Path | None = None,
    robin: str | Path | None = None,
    show_scores: bool = True,
    evidence: bool = True,
    concurrency: int = 6,
    adversarial: bool = True,
    egress_env: bool = False,
    cloaking: bool = False,
    direct: bool = False,
) -> SuiteResult:
    """Run the full chain from one case file."""
    from paytrace import AdsTxtIndex, Fetcher
    from paytrace.collectors import build_all

    scope = CaseScope.load(case_path)
    out = Path(outdir)
    secure_mkdir(out)

    trail = Trail(scope.case_ref, scope.authorization)
    warnings: list[str] = []
    stats: dict[str, Any] = {}

    if blacklist:
        load_blacklist(str(blacklist))

    ua = (f"attribution-suite/{_VERSION} (case {scope.case_ref}; "
          f"{scope.contact_email or 'no-contact-configured'})")
    policy = PolicyEngine(policy=RobotsPolicy(scope.robots_policy), user_agent=ua)
    ev = EvidenceLog(scope, out / "evidence") if evidence else None

    # Evidence and policy are transport dependencies, not decorations. Building
    # them and not passing them meant a run made real requests, wrote an empty
    # evidence package, and reported evidence_verified=True -- and a case file
    # saying `robots_policy: respect` bought nothing.
    fetcher = Fetcher(
        user_agent=ua, max_requests=scope.max_requests,
        # Inside the case output, so retrieved content lives and dies with the
        # investigation rather than in whatever directory the CLI was run from.
        cache_dir=Path(out) / ".cache",
        egress=_egress_pool(scope, egress_env, warnings, direct=direct),
        evidence_log=ev, policy=policy,
    )

    collectors = build_all(fetcher, scope)
    engine = Engine(scope, collectors=collectors, concurrency=concurrency)

    # Blocked retrievals are surfaced, not just counted. A run where SSRF, the
    # fetch policy or a size cap stopped everything reported "no entity
    # resolved above threshold" -- indistinguishable from an honest negative,
    # which is the worst way for a collection failure to present.
    def _report_absent() -> None:
        """Checks that COMPLETED and found nothing. Distinct from blocked."""
        absent = list(getattr(fetcher, "checked_absent", ()) or ())
        if not absent:
            return
        warnings.append(
            f"{len(absent)} retrieval(s) completed and found nothing (checked, "
            "not blocked):")
        for url, why in absent[:5]:
            warnings.append(f"    checked: {url} — {why}")

    def _report_partial() -> None:
        """Retrievals that completed but were cut at the byte cap.

        Not blocks -- content came back and evidence was written -- but a
        reader judging completeness needs them, because a conclusion drawn
        from the first 10 MB of a 104 MB document rests on a fraction of it.
        """
        partial = list(getattr(fetcher, "truncated_reads", ()))
        if not partial:
            return
        warnings.append(
            f"{len(partial)} retrieval(s) completed but were cut at the byte "
            "cap, so only the leading portion was read (the streaming lookup "
            "covers sellers.json; anything else was read in part):")
        for url, why in partial[:4]:
            warnings.append(f"    partial: {url} — {why}")

    def _report_blocked() -> None:
        if not fetcher.blocked:
            return
        reasons: dict[str, int] = {}
        for _url, reason in fetcher.blocked:
            key = reason.split(":")[0].split("(")[0].strip()[:60]
            reasons[key] = reasons.get(key, 0) + 1
        warnings.append(
            f"{len(fetcher.blocked)} retrieval(s) were blocked and produced no "
            f"evidence: " + ", ".join(f"{k} ({n})" for k, n in
                                      sorted(reasons.items(), key=lambda kv: -kv[1])))
        # WHICH url was blocked, not just how many. Without this an absence is
        # undiagnosable: a run that could not reach the file naming the payee
        # looks identical to one where a stylesheet was skipped.
        for url, why in fetcher.blocked[:8]:
            warnings.append(f"    blocked: {url} — {why}")
        if len(fetcher.blocked) > 8:
            warnings.append(f"    ... and {len(fetcher.blocked) - 8} more "
                            "(full list in verification_trail.json)")
        if fetcher.count == 0:
            warnings.append(
                "NO retrieval succeeded. Any absence in this report means the "
                "source was not reached, not that it holds no record.")

    ads_index = None
    if corpus_index:
        ads_index = AdsTxtIndex(str(corpus_index))
        engine.index = CompositeIndex(ads_index, InMemoryIndex(engine.graph))
    else:
        warnings.append(
            "no corpus index supplied — selectivity counts come from this case "
            "only, so confidence figures are upper bounds, not assessments")

    for s in scope.seeds:
        trail.seed(s)

    # --- imported claims, before collection, so they seed the frontier ------ #
    if spiderfoot:
        from paytrace import from_spiderfoot_csv, from_spiderfoot_db
        p = Path(spiderfoot)
        imported = (from_spiderfoot_db(p) if p.suffix in (".db", ".sqlite")
                    else from_spiderfoot_csv(p))
        for c in imported:
            engine.ingest(c, depth=1)
        stats["spiderfoot_claims"] = len(imported)
        trail.add_imported("SpiderFoot", str(p), len(imported))

    if opencti:
        from paytrace import from_opencti_bundle
        imported = from_opencti_bundle(Path(opencti))
        for c in imported:
            engine.ingest(c, depth=1)
        stats["opencti_claims"] = len(imported)
        trail.add_imported("OpenCTI", str(opencti), len(imported))

    robin_claims: list = []
    if robin:
        from paytrace import from_robin
        robin_claims = from_robin(Path(robin))
        for c in robin_claims:
            engine.ingest(c, depth=1)
        stats["robin_claims"] = len(robin_claims)
        onion = sum(1 for c in robin_claims if c.raw.get("onion"))
        llm = sum(1 for c in robin_claims if c.raw.get("llm_derived"))
        stats["robin_onion_sources"] = onion
        trail.add_imported("Robin", str(robin), len(robin_claims))
        trail.add(
            StepKind.FILTER,
            f"Marked {len(robin_claims)} Robin claim(s) as derived text",
            detail=("Robin truncates scraped content and does not retain response "
                    "bodies, so these are leads rather than captures. "
                    f"{onion} came from .onion sources, which cannot be re-fetched "
                    f"or archived; {llm} were asserted by a language model and are "
                    "capped at UNCERTAIN."))
        if onion:
            warnings.append(
                f"{onion} claim(s) derive from .onion sources with no preserved "
                "response body — unverifiable after the fact by anyone, including you")

    # --- collect ------------------------------------------------------------ #
    # Cleanup under finally: an unexpected exception used to leave HTTP
    # clients and temporary cache directories open.

    # One event loop for the work AND the cleanup. Closing the fetcher in a
    # second asyncio.run() fails on a real network -- its connections belong
    # to the first loop, now closed -- with "Event loop is closed". Mock
    # transports hold no such connections, which is why tests never saw it.
    # --- adversarial pass --------------------------------------------------- #
    # Runs between collection and scoring, which is the only window where it
    # can matter: `resolve` consumes claim weights, so an identifier demoted
    # after scoring has already counted in full. This module existed and was
    # called by nothing but the demo script, so every real run scored planted
    # identifiers at face value while the header of this file said otherwise.
    adv_state: dict[str, Any] = {"assessed": 0, "demoted": 0, "verdicts": {},
                                 "skipped": ""}

    link_state: dict[str, Any] = {}
    pf_state: dict[str, Any] = {}

    def _link_gravatars(graph) -> None:
        """Bind observed emails to observed avatar hashes, by arithmetic.

        Runs in the same window as the adversarial pass -- after collection,
        before scoring -- because it adds claims, and claims added after
        scoring have already been left out of it. Costs no requests: the
        binding is md5 of an address we already hold.
        """
        from paytrace.gravatar_link import link_gravatars

        report = link_gravatars(graph)
        link_state["report"] = report
        for c in report.claims:
            graph.add_claim(c)
        for address, digest in report.matched:
            trail.add(StepKind.EXTRACT,
                      "an observed email matches an observed Gravatar hash",
                      detail=f"{address} -> {digest} "
                             "(md5 of the lowercased, trimmed address; no "
                             "request was made)")

    async def _adversarial(graph) -> None:
        from paytrace.adversarial import apply_assessments, assess_identifier

        candidates = _assessable_identifiers(graph, scope, ads_index,
                                             limit=ADVERSARIAL_MAX)
        if not candidates:
            adv_state["skipped"] = "no analytics or seller identifiers collected"
            return
        # "More established" needs a corpus to mean anything: how many ad
        # accounts a property declares separates a scraper from a publisher.
        holder_counts: dict[str, int] = {}
        if ads_index is not None:
            for _i, _s, _v, _a, _sid, sharing in candidates:
                for d in sharing:
                    if d not in holder_counts:
                        with contextlib.suppress(Exception):
                            holder_counts[d] = len(ads_index.sellers_for_domain(d))
        assessments = {}
        for ident, scheme, value, adsys, sid, sharing in candidates:
            try:
                a = await assess_identifier(
                    fetcher, _seed_domain(scope) or ident, scheme, value,
                    adsystem=adsys, seller_id=sid,
                    sharing_domains=sharing,
                    holder_counts=holder_counts)
            except Exception as e:                  # a check must not end a run
                trail.add(StepKind.INFERENCE,
                          f"planted-identifier check could not run for "
                          f"{value}: {type(e).__name__}: {e}",
                          detail="the identifier keeps its original weight")
                continue
            # Keyed by the identifier AS THE GRAPH HOLDS IT ("ga4:G-XXXX"),
            # not by the bare value the checks take. Keyed by the bare value,
            # nothing matched and the pass assessed identifiers it then failed
            # to demote -- visible only as assessed>0 with demoted=0.
            assessments[ident] = a
            adv_state["verdicts"][a.verdict.value] = (
                adv_state["verdicts"].get(a.verdict.value, 0) + 1)
        apply_assessments(graph.claims, assessments)
        adv_state["assessed"] = len(assessments)
        adv_state["demoted"] = sum(
            1 for c in graph.claims if c.raw.get("demoted"))
        for ident, a in assessments.items():
            if a.should_demote:
                # `Trail.filtered` is the helper for "set aside, with reason",
                # which is exactly what a demotion is. Hand-rolling the same
                # step is how the two drift: this one carried no citation and
                # phrased the reason differently from every other set-aside in
                # the trail.
                trail.filtered(f"{ident} ({a.verdict.value})",
                               "; ".join(a.reasons))

    # --- cloaking -------------------------------------------------------- #
    # The whole method rests on ads.txt being a public declaration. Nothing
    # compared our copy against anyone else's, so a site serving one file to
    # this tool and another to a browser defeated every check downstream of
    # the retrieval -- and the report would read as though the declaration
    # were the site's, when it was only ours.
    cloak_state: dict[str, Any] = {}

    async def _cloaking_check() -> None:
        from paytrace.cloaking import BROWSER_UA, check_cloaking

        seed = _seed_domain(scope)
        if not seed:
            return
        labels = tuple(e.label for e in fetcher.egress.egresses
                       if e.label != fetcher.egress.default_label)[:1]
        report = await check_cloaking(
            fetcher, seed,
            browser_ua=BROWSER_UA if cloaking else "",
            egress_labels=labels)
        cloak_state["report"] = report
        if report.misrepresented:
            # Recorded, always. A tool that claims an honest record while
            # quietly misrepresenting itself is worth less than one that
            # cannot run the check.
            trail.add(StepKind.FETCH,
                      "ads.txt was retrieved a second time with a browser "
                      "User-Agent, to test whether the declaration depends on "
                      "who is asking",
                      detail=f"User-Agent sent: {BROWSER_UA}")

    async def _preflight() -> None:
        """Establish what the vantage point actually is, before using it.

        One request, non-fatal. A proxy that fails open attributes every
        capture in the package to an exit the traffic never used, and finding
        that in the manifest afterwards is too late -- the retrievals have
        already happened from somewhere the case may have been written to
        avoid.
        """
        from paytrace import preflight

        pool = fetcher.egress
        if len(pool.egresses) == 1 and pool.get().is_direct:
            return              # nothing to establish: direct is direct
        try:
            pf = await preflight(fetcher, pool)
        except Exception as e:  # noqa: BLE001 - a check must not end a run
            warnings.append(f"egress preflight could not run: "
                            f"{type(e).__name__}: {e}")
            return
        pf_state["report"] = pf
        if pf.failed_open:
            warnings.append("EGRESS FAILED OPEN: " + pf.render())
            trail.add(StepKind.FETCH,
                      "Egress preflight found the configured proxy is not "
                      "carrying this run's traffic",
                      detail=pf.render())
        elif not pf.reached:
            warnings.append(pf.render())
        else:
            trail.add(StepKind.FETCH, "Egress preflight", detail=pf.render())

    async def _collect():
        try:
            await _preflight()
            if cloaking or len(fetcher.egress.egresses) > 1:
                await _cloaking_check()
            async def _before_resolve(graph) -> None:
                # Order matters: link first, so a binding the adversarial
                # checks should demote is present to be demoted.
                _link_gravatars(graph)
                if adversarial:
                    await _adversarial(graph)

            return await engine.run(before_resolve=_before_resolve)
        finally:
            await fetcher.aclose()

    resolution = asyncio.run(_collect())
    stats["requests"] = fetcher.count
    linked = link_state.get("report")
    if linked is not None and linked.matched:
        stats["gravatar_links"] = len(linked.matched)
        warnings.append(
            f"{len(linked.matched)} email(s) were bound to an avatar hash "
            "found on a page, by computing md5 of the address — no requests. "
            "The arithmetic is certain; what it shows is that the page "
            "displays that address's avatar, which anyone who knows the "
            "address can arrange. It is not treated as independent "
            "corroboration.")

    if getattr(fetcher, "tls_intercepted", ""):
        stats["tls_intercepted"] = True
        warnings.append(
            "TLS was terminated by this environment's proxy, whose CA bundle "
            f"({fetcher.tls_intercepted}) was trusted so that HTTPS could work "
            "at all here. Every capture in this package therefore proves what "
            "that proxy served, not that the origin served it. For a result "
            "someone else must be able to rely on, re-run from a vantage point "
            "that does not intercept.")

    pf = pf_state.get("report")
    if pf is not None:
        # Only when the probe actually established something. Recording
        # "unreported" for a probe that could not run put a placeholder in the
        # stats and then a caveat naming it in the report -- a statement about
        # the vantage point built from the absence of one.
        if pf.reached and pf.apparent_address:
            stats["egress_vantage"] = pf.apparent_address
        if pf.failed_open:
            stats["egress_failed_open"] = True

    report = cloak_state.get("report")
    if report is not None:
        stats["ads_txt_consistency"] = report.verdict
        if report.cloaked:
            warnings.append(
                "SELECTIVE SERVING: " + report.summary() + ". "
                + "; ".join(report.differences[:4])
                + ". Treat every conclusion below as describing the copy this "
                  "tool was shown, not the site's public declaration.")
            trail.add(StepKind.INFERENCE,
                      "ads.txt differs depending on who retrieves it",
                      detail="; ".join(report.differences[:6]))
        elif report.verdict == "unverified" and cloaking:
            warnings.append(
                "the ads.txt consistency check could not run: "
                + "; ".join(report.reasons[:1]))

    if adversarial:
        stats["adversarial_assessed"] = adv_state["assessed"]
        stats["adversarial_demoted"] = adv_state["demoted"]
        if adv_state["verdicts"]:
            stats["adversarial_verdicts"] = ", ".join(
                f"{k}={v}" for k, v in sorted(adv_state["verdicts"].items()))
        if adv_state["demoted"]:
            warnings.append(
                f"{adv_state['demoted']} claim(s) were demoted to weight 0 by "
                "the planted-identifier checks: the identifier is present but "
                "is not load-bearing, not reciprocated, or has no history. "
                "They are kept in the record, because manufacturing "
                "attribution is itself evidence about the operator.")
    else:
        stats["adversarial"] = "skipped (--no-adversarial)"
        warnings.append(
            "planted-identifier checks were skipped, so an identifier planted "
            "to manufacture a link counts at full weight in this result")

    # --- handles ------------------------------------------------------------ #
    if handles or robin_claims:
        from handle_correlation import HandleCorpus, Observation, all_claims, correlation_points
        from handle_correlation import load as load_handles

        obs = load_handles(handles) if handles else []

        # Handles Robin surfaced join the same correlation pass, carrying the
        # durable identifiers found on the same page -- which is what can lift
        # them above the correlation-point floor.
        if robin_claims:
            from paytrace import to_handle_observations
            for row in to_handle_observations(robin_claims, scope.case_ref):
                obs.append(Observation(
                    handle=row["handle"], platform=row["platform"],
                    linked={k[5:]: v for k, v in row.items() if k.startswith("link_")},
                    source_url=row.get("source_url", ""),
                    case_ref=row.get("case_ref", ""),
                ))
        corpus = HandleCorpus()
        for o in obs:
            corpus.observe(o.qualified)
        hclaims = all_claims(obs, corpus)
        for c in hclaims:
            engine.graph.add_claim(c)
        hc = correlation_points(hclaims)
        stats["handle_observations"] = len(obs)
        stats["handle_confidence"] = f"{hc.level.value} ({hc.points} points)"
        warnings.append(f"handle correlation: {hc.caveat}")
        trail.infer(f"Handle correlation assessed: {hc.level.value}",
                    basis=hc.caveat)

    # --- evidence policy block ---------------------------------------------- #
    # Validity and completeness are properties of the COLLECTION, not of
    # evidence capture. These lived inside `if evidence:`, so a run with
    # --no-evidence returned stats omitting result_valid/result_complete
    # entirely -- and a caller reading `.get("result_valid") is not False`
    # treated a failed run as fine.
    stats["result_valid"] = True
    stats["result_complete"] = True

    # Blocked retrievals are a property of COLLECTION, not of evidence capture.
    # This lived inside `if ev is not None:`, so `--no-evidence` turned
    # "collection was blocked" into "checked and found nothing" -- a run where
    # URL safety blocked everything reported requests=0, claims=0,
    # result_valid=true and no warning at all.
    blocked = list(getattr(fetcher, "blocked", []))
    if blocked:
        stats["collection_blocked"] = len(blocked)
        # Where the blocks fell matters. A run whose every block is a robots
        # refusal on a probe path, or a dead host listed in someone else's
        # ads.txt, is not missing anything about the subject -- but it raised
        # the same INCOMPLETE banner as a run that could not reach the file
        # naming the payee.
        seed_hosts = {str(sd).split(":", 1)[1].lower()
                      for sd in (getattr(scope, "seeds", ()) or ())
                      if str(sd).startswith("domain:")}

        def _on_seed(url: str) -> bool:
            host = urlparse(url).hostname or ""
            host = host.lower().removeprefix("www.")
            return any(host == h or host.endswith("." + h) for h in seed_hosts)

        # Per collector, so a dead one is visible. 36 collectors run; a total of
        # "378 blocked" cannot say which produced nothing, every time.
        lost = dict(getattr(fetcher, "blocked_by_collector", {}) or {})
        produced: dict[str, int] = {}
        for c in getattr(engine.graph, "claims", ()) or ():
            name = str(getattr(c, "collector", "") or "")
            if name:
                produced[name] = produced.get(name, 0) + 1
        dead = sorted(n for n, k in lost.items()
                      if k and not produced.get(n)
                      and n not in ("unknown", "fetch_policy"))
        stats["collectors_with_claims"] = len(produced)
        if dead:
            stats["collectors_that_produced_nothing"] = ", ".join(dead)
        # Hosts this machine could not reach AT ALL. A sandbox with an egress
        # allowlist produces one of these per host it does not permit, and the
        # run otherwise reports a cascade of "blocked" that reads as though the
        # sites refused us. Naming the hosts turns it into something the
        # operator can act on -- it is the list to allowlist.
        unreachable: dict[str, str] = {}
        for url, reason in blocked:
            low = str(reason).lower()
            if any(m in low for m in ("does not resolve", "proxy",
                                      "tunnel connection failed",
                                      "network is unreachable",
                                      "no route to host", "from this machine")):
                host = urlparse(url).hostname or ""
                if host and host not in unreachable:
                    unreachable[host] = str(reason)[:80]
        if unreachable:
            stats["hosts_unreachable"] = len(unreachable)
            listed = ", ".join(sorted(unreachable)[:8])
            more = (f" and {len(unreachable) - 8} more"
                    if len(unreachable) > 8 else "")
            warnings.append(
                f"{len(unreachable)} host(s) could not be reached from this "
                f"machine at all: {listed}{more}. This is a condition of the "
                "environment, not a rule published by those sites. If you are "
                "running inside a sandbox with an egress allowlist, that is "
                "the list of hosts this chain needs.")

        stats["blocked_on_subject"] = sum(1 for u, _ in blocked if _on_seed(u))
        stats["blocked_elsewhere"] = len(blocked) - stats["blocked_on_subject"]
        stats["result_complete"] = False
        _report_blocked()
        warnings.append(
            f"{len(blocked)} retrieval(s) were blocked by policy or URL "
            "safety. The result is INCOMPLETE: an absence in this report may "
            "mean 'could not be checked' rather than 'checked and not found'.")

    # A cut body is reported whether or not anything was blocked: a run with
    # no blocks at all can still have read one tenth of the document that
    # names the payee.
    _report_partial()

    # Completed checks that found nothing: reported, but they do NOT make the
    # result incomplete.
    _report_absent()

    if getattr(engine, "budget_exhausted", False):
        stats["budget_exhausted"] = True
        stats["result_complete"] = False
        warnings.append(
            f"the declared request budget ({scope.max_requests}) stopped this "
            "run: the result is INCOMPLETE, not invalid. Everything collected "
            "is sound; there is simply less of it.")

    if getattr(engine, "runtime_exhausted", False):
        stats["runtime_exhausted"] = True
        stats["result_complete"] = False
        warnings.append(
            f"the wall-clock budget ({scope.max_runtime_s}s) stopped traversal "
            "with work still queued: the result is INCOMPLETE. A time limit "
            "that shortens the search while the result reports complete can "
            "change an attribution conclusion.")

    if getattr(engine, "nodes_truncated", False):
        stats["nodes_truncated"] = True
        stats["result_complete"] = False
        warnings.append(
            "the node budget stopped frontier expansion: the result is "
            "INCOMPLETE. A safety limit that silently shortens the search "
            "while the result looks complete can change a conclusion.")

    defects = list(getattr(engine, "internal_defects", []))
    if defects:
        stats["internal_defects"] = len(defects)
        stats["result_valid"] = False
        warnings.append(
            f"{len(defects)} internal defect(s) during collection "
            f"({', '.join(sorted({d['collector'] for d in defects}))}). "
            "This result is INVALID: collectors failed for reasons that are "
            "bugs in the toolkit, not source unavailability, so the evidence "
            "set is incomplete in an unknown way.")

    if ev is not None:
        ev.fetch_policy = policy.summary(fetcher.policy_decisions)
        paths = write_evidence_package(ev)
        ok, problems = ev.verify()
        _report_blocked()
        stats["evidence_verified"] = ok
        # Reconcile fetches against captures. An empty package is internally
        # consistent and verifies happily, so "verified" must also mean
        # "complete" or it is a false assurance.
        # An internal defect must not produce a normal-looking report. The
        # engine catches unexpected exceptions so one bad collector does not
        # end a run, but a result computed with silently-missing collectors is
        # not a weaker result -- it is an unknown one.
        stats["fetches"] = fetcher.count
        stats["blocked"] = len(fetcher.blocked)
        stats["captures"] = len(ev.captures)
        if fetcher.count and not ev.captures:
            ok = False
            stats["evidence_verified"] = False
            warnings.append(
                f"evidence package is EMPTY after {fetcher.count} network "
                "request(s) — captures were not recorded, so the package "
                "attests to nothing")
        elif len(ev.captures) < fetcher.count:
            warnings.append(
                f"{fetcher.count} request(s) but {len(ev.captures)} capture(s): "
                "some retrievals are unrecorded")
        if not ok:
            warnings.append(
                "EVIDENCE VERIFICATION FAILED — do not present these findings: "
                + "; ".join(problems[:3]))

    # Status travels into the report so a shared document carries it.
    outputs = write_all(engine.graph, resolution, scope, out, status=stats,
                        show_scores=show_scores, trail=trail)
    if ev is not None:
        outputs += paths

    return SuiteResult(scope=scope, graph=engine.graph, resolution=resolution,
                       trail=trail, evidence=ev, outputs=outputs,
                       warnings=warnings, stats=stats)

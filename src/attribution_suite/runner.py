"""Unified run: collect, score, resolve, verify, report.

One case file drives everything. The sequence is fixed because the ordering
matters: adversarial checks must run before scoring (a planted identifier must
not reach the model at full weight), and evidence verification must run before
reporting (findings from a package that failed its integrity check should never
be presented).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
            L.append("  warnings:")
            L += [f"    - {w}" for w in self.warnings]
        return "\n".join(L)


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
) -> SuiteResult:
    """Run the full chain from one case file."""
    from paytrace import AdsTxtIndex, EgressPool, Fetcher
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
        egress=EgressPool.from_case(getattr(scope, "egress", None)),
        evidence_log=ev, policy=policy,
    )

    collectors = build_all(fetcher, scope)
    engine = Engine(scope, collectors=collectors, concurrency=concurrency)

    # Blocked retrievals are surfaced, not just counted. A run where SSRF, the
    # fetch policy or a size cap stopped everything reported "no entity
    # resolved above threshold" -- indistinguishable from an honest negative,
    # which is the worst way for a collection failure to present.
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
        if fetcher.count == 0:
            warnings.append(
                "NO retrieval succeeded. Any absence in this report means the "
                "source was not reached, not that it holds no record.")

    if corpus_index:
        engine.index = CompositeIndex(AdsTxtIndex(str(corpus_index)),
                                      InMemoryIndex(engine.graph))
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
    async def _collect():
        try:
            return await engine.run()
        finally:
            await fetcher.aclose()

    resolution = asyncio.run(_collect())
    stats["requests"] = fetcher.count

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
        stats["result_complete"] = False
        _report_blocked()
        warnings.append(
            f"{len(blocked)} retrieval(s) were blocked by policy or URL "
            "safety. The result is INCOMPLETE: an absence in this report may "
            "mean 'could not be checked' rather than 'checked and not found'.")

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

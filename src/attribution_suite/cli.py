"""Unified CLI.

    attribution run       --case case.yaml [--index paytrace.sqlite] [--handles h.csv]
    attribution portfolio <domain> --index paytrace.sqlite
    attribution handles   --observations h.csv
    attribution registries --jurisdiction IN
    attribution verify    ./out/evidence
    attribution version
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path

from .investigator import Refused
from .runner import run_case
from .version import banner, versions

#: What a `--domain` run asserts when the analyst asserts nothing. The case
#: file normally carries an authorization reference, and that control exists so
#: a human states the authority under which a subject is investigated. A
#: one-command convenience must not fake one, so it records its own absence --
#: visibly, in the audit trail and the report.
UNATTESTED = "unattested: --domain convenience invocation, no authority asserted"


def _synth_case(domain: str, authorization: str | None, outdir: Path) -> Path:
    """Write the case file a `--domain` run implies.

    Defaults are deliberately modest: one pivot hop and a bounded request
    budget, so a one-liner cannot start an unbounded crawl of someone's estate.
    Write a real case file for anything larger.
    """
    import datetime

    outdir.mkdir(parents=True, exist_ok=True)
    case = outdir / "case.yaml"
    # Built with the YAML library, not string formatting: an authorization
    # reference is free text and routinely contains a colon ("ticket: 4821"),
    # which silently produces an invalid document when hand-formatted.
    import yaml

    case.write_text(yaml.safe_dump({
        "case_ref": f"{domain}-{datetime.date.today():%Y%m%d}",
        "authorization": authorization or UNATTESTED,
        "seeds": [f"domain:{domain}"],
        "entity_types_allowed": ["Company"],
        "audit_path": str(outdir / "audit.jsonl"),
        "robots_policy": "respect",
        "pivot_radius": 1,
        "max_requests": 200,
    }, sort_keys=False))
    return case


def _mask(value: str, show_person: bool) -> str:
    """Hide personal identifiers in the ON-SCREEN summary.

    Nothing is removed from the record: the preserved evidence and the graph
    keep what was collected. This masks only the summary printed to the
    terminal — the thing shown on a projector or pasted into a ticket — because
    a live domain readily yields a real person's address. The toolkit treats
    conclusions about people as leads, not findings.
    """
    if show_person:
        return value
    kind, _, rest = value.partition(":")
    if kind in ("email", "person_name", "phone"):
        return f"{kind}:[withheld from this summary; --show-person to display]"
    return value


def _print_findings(res, show_person: bool = False) -> None:
    """The point of the one-command form: show the chain, not just counts."""
    resolution = getattr(res, "resolution", None)
    assessments = dict(getattr(resolution, "assessments", {}) or {})
    print("\nFINDINGS")
    if not assessments:
        print("  no entity resolved above threshold")
        print("  (a site with no ads.txt publishes no payee; that is a result, "
              "not a failure)")
        return
    ranked = sorted(assessments.items(), key=lambda kv: kv[1].log_odds, reverse=True)
    for (subject, obj), a in ranked[:5]:
        print(f"  {_mask(subject, show_person)}  ->  {_mask(obj, show_person)}")
        print(f"      {a.band.value}  ({a.estimative}) — "
              f"{a.independent_groups} independent evidence group(s)")
        for line in list(a.top_evidence)[:3]:
            print(f"        - {line}")
    print("\n  Bands order evidence strength. They are not probabilities, and a "
          "conclusion\n  about a person is a lead until a statutory register "
          "confirms it.")


def _run(a: argparse.Namespace) -> int:
    from .version import aligned, versions

    if not aligned():
        print(f"refusing to run: mixed package versions {versions()}. "
              "The four packages are released together; a mixed install can "
              "produce evidence whose fields mean different things to "
              "different components. Reinstall them at one version.",
              file=sys.stderr)
        return 5

    if a.concurrency < 1:
        print(f"--concurrency must be at least 1, got {a.concurrency}. "
              "A zero-permit semaphore hangs rather than disabling "
              "concurrency.", file=sys.stderr)
        return 2

    if getattr(a, "screenshots", False):
        print(
            "--screenshots is not available in this release.\n"
            "\n"
            "The capture module exists and is tested, but it is not wired into "
            "`attribution run`, and it navigates URLs with a browser from "
            "inside the inference package -- outside the SSRF guard and "
            "contrary to that package's no-network-IO boundary. Shipping a "
            "flag that silently does nothing would be worse.\n"
            "\n"
            "Use attribution_graph.ScreenshotCapturer directly if you accept "
            "those limitations.",
            file=sys.stderr)
        return 2

    case_path = a.case
    if a.domain:
        if a.case:
            print("use --case or --domain, not both", file=sys.stderr)
            return 2
        case_path = _synth_case(a.domain, a.authorization,
                                Path(a.out))
        print(f"case file written: {case_path}")
        if not a.authorization:
            print("authorization: none asserted — recorded as unattested in the "
                  "audit trail")
    elif not a.case:
        print("give --case FILE or --domain DOMAIN", file=sys.stderr)
        return 2

    from attribution_graph import PolicyError
    try:
        res = run_case(
            case_path, a.out, corpus_index=a.index or None, blacklist=a.blacklist or None,
            handles=a.handles or None, spiderfoot=a.spiderfoot or None,
            opencti=a.opencti or None, robin=a.robin or None,
            show_scores=not a.no_scores,
            evidence=not a.no_evidence, concurrency=a.concurrency,
        )
    except PolicyError as e:
        print(f"policy error: {e}", file=sys.stderr)
        return 2

    print(banner())
    print(res.summary())
    _print_findings(res, a.show_person)
    print()
    for p in res.outputs:
        print(f"  {p}")
    # Non-zero when integrity failed: a caller scripting this must be able to
    # detect that the findings are not presentable.
    # Machine-readable exit semantics. This returned 0 for any run whose
    # evidence verified, so an INVALID or INCOMPLETE investigation looked like
    # success to a CI job, a wrapper script or an MCP host.
    #
    #   0  valid and complete
    #   3  incomplete (a budget stopped the search)
    #   4  invalid (an internal defect corrupted collection)
    #   1  evidence verification failed
    if res.stats.get("result_valid") is False:
        print("\nEXIT 4 — RESULT INVALID: internal defects occurred during "
              "collection. Do not rely on this output.", file=sys.stderr)
        return 4
    if res.stats.get("evidence_verified") is False:
        print("\nEXIT 1 — evidence package failed verification.", file=sys.stderr)
        return 1
    if res.stats.get("result_complete") is False:
        print("\nEXIT 3 — RESULT INCOMPLETE: a budget stopped the search. "
              "Everything collected is sound; there is less of it than an "
              "unbudgeted run would have produced.", file=sys.stderr)
        return 3
    return 0


def _ask(a: argparse.Namespace) -> int:
    """Natural-language entry point across the whole toolchain."""
    from .investigator import Investigator

    try:
        inv = Investigator(
            api_key=a.api_key or None, model=a.model,
            corpus_index=a.index or None, offline=a.offline,
        ).investigate(a.question, out=a.out or None)
    except Refused as e:
        print(str(e), file=sys.stderr)
        return 3

    print(f"planner:  {inv.planner}")
    print(f"packages: {', '.join(inv.packages_used) or '—'}")
    print(f"subject:  {inv.intent.kind}:{inv.intent.subject or '—'}")
    print()
    print(inv.report)
    if inv.warnings:
        print("\nWARNINGS")
        for w in inv.warnings:
            print(f"  - {w}")
    for p in inv.outputs:
        print(f"  {p}")
    return 0


def _verify(a: argparse.Namespace) -> int:
    """Verify an evidence package using trusted, installed code.

    This used to run the `verify.py` found *inside* the evidence directory. An
    evidence package is untrusted input by definition -- it is the thing under
    examination, usually produced by someone else -- so the verification
    workflow itself invited arbitrary code execution. A benign reproduction
    swapped in a script that wrote a marker file and the normal workflow ran it.

    The generated standalone verify.py still ships for dependency-free manual
    use; a reader can inspect it before running it. The installed command never
    executes it.
    """
    from attribution_graph import verify_package

    result = verify_package(a.path)
    print(result.render())
    if (Path(a.path) / "verify.py").exists():
        print()
        print("note: this package also contains its own verify.py. It was NOT "
              "executed. Running a script supplied inside an untrusted evidence "
              "package carries ordinary arbitrary-code risk; read it first.")
    return 0 if result.ok else 1



def _delegate(module: str, argv: list[str]) -> int:
    from importlib import import_module
    return import_module(module).main(argv)


def _version(_: argparse.Namespace) -> int:
    for k, v in versions().items():
        print(f"{k.replace('_', '-'):22} {v}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="attribution",
        description="Unified entity attribution suite")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="full chain from one case file")
    r.add_argument("--case")
    r.add_argument("--domain", help="investigate one domain; writes the case file for you")
    r.add_argument("--authorization", help="the authority under which you are investigating")
    r.add_argument("--show-person", action="store_true",
                   help="show personal identifiers in the on-screen summary")
    r.add_argument("--out", default="./out")
    r.add_argument("--index", default="", help="ads.txt corpus sqlite")
    r.add_argument("--blacklist", default="")
    r.add_argument("--handles", default="", help="observed-handle CSV/JSON")
    r.add_argument("--spiderfoot", default="", help="SpiderFoot .csv or .db")
    r.add_argument("--opencti", default="", help="STIX 2.1 bundle")
    r.add_argument("--robin", default="", help="Robin dark web investigation JSON")
    # Withdrawn for this release. The flags were accepted and did nothing:
    # run_case() had no screenshot path. Worse, the capture module lives inside
    # attribution-graph and navigates URLs with a browser, which breaks the
    # "the inference core performs no network I/O" boundary and puts browser
    # subresource requests outside the SSRF guard.
    #
    # Kept as an explicit error rather than removed, so a user with the flag in
    # a script learns why instead of watching it silently do nothing.
    # --screenshots is retained only to fail loudly for anyone with it in a
    # script (see _run). --screenshot-viewport was removed outright: with the
    # feature withdrawn it configured nothing, and a flag that configures
    # nothing is a flag that lies.
    r.add_argument("--screenshots", action="store_true",
                   help=argparse.SUPPRESS)
    r.add_argument("--no-scores", action="store_true",
                   help="omit confidence figures from the report")
    r.add_argument("--no-evidence", action="store_true")
    r.add_argument("--concurrency", type=int, default=6,
                   help="parallel collectors; must be >= 1")
    r.set_defaults(func=_run)

    q = sub.add_parser("ask", help="natural-language investigation across the toolchain")
    q.add_argument("question")
    q.add_argument("--api-key", default="", help="or set ANTHROPIC_API_KEY")
    q.add_argument("--model", default="claude-sonnet-4-5")
    q.add_argument("--index", default="", help="corpus sqlite")
    q.add_argument("--out", default="")
    q.add_argument("--offline", action="store_true", help="fixtures, no network")
    q.set_defaults(func=_ask)


    v = sub.add_parser("verify", help="check an evidence package's integrity")
    v.add_argument("path", default="./out/evidence", nargs="?")
    v.set_defaults(func=_verify)

    sub.add_parser("version").set_defaults(func=_version)

    known = {"run", "ask", "verify", "version"}
    argv = list(sys.argv[1:] if argv is None else argv)

    # Pass through to the component CLIs rather than duplicating their flags.
    if argv and argv[0] not in known:
        delegates = {
            "portfolio": ("paytrace.cli", argv),
            "registries": ("paytrace.cli", argv),
            "index": ("paytrace.index", argv[1:]),
            "handles": ("handle_correlation.cli", ["score"] + argv[1:]),
        }
        if argv[0] in delegates:
            mod, passthru = delegates[argv[0]]
            if argv[0] == "index":
                from paytrace.index import _main
                return _main(passthru)
            return _delegate(mod, passthru)

    args = ap.parse_args(argv)
    return args.func(args)


def _cli() -> int:
    try:
        return main()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except KeyboardInterrupt:
        with contextlib.suppress(Exception):
            print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(_cli())


def _suite_cli() -> None:
    """`attribution-suite --domain example.com` — the one-command entry point.

    Identical to `attribution run`; it exists because the common case is one
    domain and should not require remembering a subcommand.
    """
    argv = sys.argv[1:]
    if not argv or argv[0].startswith("-"):
        argv = ["run", *argv]
    sys.exit(main(argv))

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

from .install import extra_hint, install_mode
from .investigator import Refused
from .runner import run_case
from .version import banner, versions

#: What a `--domain` run asserts when the analyst asserts nothing. The case
#: file normally carries an authorization reference, and that control exists so
#: a human states the authority under which a subject is investigated. A
#: one-command convenience must not fake one, so it records its own absence --
#: visibly, in the audit trail and the report.
UNATTESTED = "unattested: --domain convenience invocation, no authority asserted"


def _synth_case(domain: str, authorization: str | None, outdir: Path,
                max_requests: int = 800, minimise: bool = False) -> Path:
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
        # The analyst's own report must be READABLE. Minimisation replaces every
        # identifier with a hash, which is right for a report you hand to someone
        # else and useless for one you are reading yourself: every name, domain
        # and account becomes `min:a5979016…`. Opt in with --minimise.
        "minimize": minimise,
        "robots_policy": "respect",
        # Two hops, not one. The answer is seed -> seller_id -> the seller's
        # OWN site, whose about/imprint page names the people. Radius 1 stops
        # at the seller account and never reaches the site that identifies it.
        "pivot_radius": 2,
        # Two hops across a few dozen declared accounts does not fit in 300:
        # a real run exhausted the budget before reaching the seller's own site,
        # which is where the people are named. Override with --max-requests.
        "max_requests": max_requests,
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


def _incomplete_reason(stats: dict, scope) -> str:
    """Why a run is INCOMPLETE, from what actually happened.

    Four different conditions set ``result_complete`` False. The exit message
    reported "a budget stopped the search" for every one of them, so a run
    halted by robots.txt sent the reader hunting for a limit to raise when
    nothing had been near a limit.
    """
    why = []
    blocked = stats.get("collection_blocked")
    if blocked:
        on_subject = stats.get("blocked_on_subject")
        elsewhere = stats.get("blocked_elsewhere")
        where = ""
        if on_subject is not None:
            where = (f" — {on_subject} on the subject itself, {elsewhere} on "
                     "third-party hosts")
        why.append(f"{blocked} retrieval(s) were blocked by fetch policy or "
                   f"URL safety (robots.txt, a size cap, or a refused host)"
                   f"{where}")
    if stats.get("budget_exhausted"):
        why.append(f"the request budget ({scope.max_requests}) was reached")
    if stats.get("runtime_exhausted"):
        why.append("the wall-clock budget was reached")
    if stats.get("nodes_truncated"):
        why.append("the node budget stopped frontier expansion")
    return "; ".join(why) or "the search did not run to completion"


#: Evidence groups that are the subject's own statements about itself. A page
#: naming a company proves the page names it, nothing more -- and an Instagram
#: viewer's terms page names Meta.
_SELF_PUBLISHED = ("imprint|", "dns_txt|", "ownerdomain|", "surface|")



def _seed_of(res) -> str:
    """The domain the question was about."""
    for seed in getattr(res.scope, "seeds", ()) or ():
        text = str(seed)
        if text.startswith("domain:"):
            return text.split(":", 1)[1]
    return ""


#: Identifier kinds that name a party. A second hop is worth showing only when
#: it reaches one of these -- seller-to-seller pairs are peers sharing an ad
#: system, not a step toward whoever is paid.
_ENTITY_KINDS = ("org_name:", "company_number:", "cik:", "lei:", "person_name:",
                 "email:", "address:",
                 # the seller's own site: its about page is what names people
                 "domain:")


def _tiers(seed: str, assessments):
    """Split into: about the seed, one hop further to a named party, the rest.

    Ranking both hops together by score put seller-to-seller pairs (8.0) above
    the site's own ads.txt declarations (5.4), so widening the search pushed the
    seed's own findings off the list entirely. Distance from the seed orders
    this, not score.
    """
    direct = {k: v for k, v in assessments.items()
              if seed and any(seed in side for side in k)}
    nodes = {side for k in direct for side in k}
    onward = {k: v for k, v in assessments.items()
              if k not in direct and nodes & set(k)
              and any(side.startswith(_ENTITY_KINDS) for side in k)}
    rest = {k: v for k, v in assessments.items()
            if k not in direct and k not in onward}
    return direct, onward, rest


def _seed_groups_are_self_published(seed: str, assessment) -> bool:
    """True when nothing independent connects THE SEED to the other side.

    A registry lookup of a name found on the subject's own page corroborates
    that the company exists, not that it operates the site. Counting it as
    independent support turned "the terms page names Meta" into
    STRONG_EVIDENCE that Meta operates the site.
    """
    groups = [g for g in _groups(assessment) if seed and seed in g]
    return bool(groups) and all(g.startswith(_SELF_PUBLISHED) for g in groups)


def _groups(assessment) -> list:
    out = []
    for item in getattr(assessment, "top_evidence", ()) or ():
        out.append(str(item[0] if isinstance(item, (tuple, list)) else item))
    return out


def _band_label(a) -> str:
    """UNSUPPORTED means "contradicted or unsupported", which reads as though
    the claim was refuted. For a single authoritative source that is wrong: the
    site's own ads.txt entry is not contradicted, it is simply not corroborated
    yet. Conflating the two is the error this toolkit exists to avoid.
    """
    band = a.band.value
    if band == "UNSUPPORTED" and getattr(a, "independent_groups", 0) >= 1:
        return ("UNCORROBORATED  (one evidence group; not contradicted, "
                "not yet corroborated)")
    return f"{band}  ({a.estimative})"


def _print_findings(res, show_person: bool = False) -> None:
    """Answer the question that was asked, about the domain that was asked.

    This ranked every assessment in the graph and printed the top five, so a
    run about one site reported links between unrelated third parties, and a
    company merely NAMED on the site's terms page could outrank the site's own
    payee. Findings about the seed come first; everything else is counted, not
    paraded.
    """
    resolution = getattr(res, "resolution", None)
    assessments = dict(getattr(resolution, "assessments", {}) or {})
    seed = _seed_of(res)

    print(f"\nFINDINGS — {seed or 'this case'}")
    if not assessments:
        print("  no entity resolved above threshold")
        print("  (a site with no ads.txt publishes no payee; that is a result, "
              "not a failure)")
        return

    direct, onward, rest = _tiers(seed, assessments)

    def show(items, limit):
        for (subject, obj), a in sorted(items.items(), key=lambda kv: kv[1].log_odds,
                                        reverse=True)[:limit]:
            print(f"  {_mask(subject, show_person)}  ->  {_mask(obj, show_person)}")
            note = ""
            if _seed_groups_are_self_published(seed, a):
                note = ("  [self-published: only the site's own pages link it "
                        "here; the other groups corroborate the entity, not the "
                        "relationship]")
            print(f"      {_band_label(a)} — "
                  f"{a.independent_groups} independent evidence group(s){note}")
            for line in list(a.top_evidence)[:3]:
                print(f"        - {line}")

    if not direct:
        print(f"  nothing resolved about {seed or 'the seed'} above threshold.")
    show(direct, 6)
    if onward:
        print("\n  WHO THOSE ACCOUNTS BELONG TO")
        show(onward, 6)
    other = rest
    if other:
        print(f"\n  {len(other)} further link(s) between third parties, not about "
              f"{seed or 'the seed'} — see attribution_report.md")

    print("\n  Bands order evidence strength. They are not probabilities, and a "
          "conclusion\n  about a person is a lead until a statutory register "
          "confirms it.")



def _payee(res, seed: str, show_person: bool) -> None:
    """Name the payee the way an analyst does it by hand.

    An ads.txt may list dozens of DIRECT accounts, most of them networks and
    resellers whose sellers.json entry covers thousands of sites. The one that
    identifies the operator is the entry whose DECLARED DOMAIN is this site:
    the ad system is stating who it pays for this inventory.
    """
    # The graph lives on the result itself; reading it from `resolution`
    # returned nothing, so this section never printed.
    graph = (getattr(res, "graph", None)
             or getattr(getattr(res, "resolution", None), "graph", None))
    claims = list(getattr(graph, "claims", ()) or ())
    if not claims or not seed:
        return

    want = seed.lower().removeprefix("www.")
    #: seller_id -> DIRECT / RESELLER, as the site's own ads.txt declares it.
    declared_here: dict[str, str] = {}

    def named(c):
        raw = getattr(c, "raw", None) or {}
        return "name_kind" in raw

    # Strongest: the ad system declares THIS site as the seller's domain.
    hits = [(c, c.raw) for c in claims if named(c)
            and str(c.raw.get("declared_domain") or "").lower()
            .removeprefix("www.") == want]
    note = ""
    if not hits:
        # Many individual sellers publish no domain at all. The account this
        # site declares in its own ads.txt still names the payee -- showing
        # nothing here just because the strongest signal is missing hid the
        # answer the run had already found.
        # Either direction: a claim may be written domain -> seller_id or
        # seller_id -> domain, and assuming one of them found nothing.
        # DIRECT vs RESELLER matters more than anything else here. A RESELLER
        # line means that ad system resells inventory sold by someone else -- it
        # is NOT the party being paid. Following a reseller names the reseller;
        # the DIRECT line names the seller.
        for c in claims:
            a = str(getattr(c.subject, "value", c.subject))
            b = str(getattr(c.object, "value", c.object))
            rel = str((getattr(c, "raw", None) or {}).get("relationship") or "")
            for one, other in ((a, b), (b, a)):
                if one.lower() == f"domain:{want}" and other.startswith("seller_id:"):
                    declared_here[other] = rel.upper() or declared_here.get(other, "")
        hits = [(c, c.raw) for c in claims if named(c)
                and str(getattr(c.subject, "value", c.subject)) in declared_here]
        # DIRECT first: the reseller lines are the ad system's own resale of
        # someone else's inventory.
        hits.sort(key=lambda h: declared_here.get(
            str(getattr(h[0].subject, "value", h[0].subject)), "") != "DIRECT")
        note = ("  (no account names this site as its own domain; these are the\n"
                "   accounts the site's ads.txt declares)")
    if not hits:
        return

    print("\nPAYEE — the ad system says it pays this party for this site")
    if note:
        print(note)
    for c, raw in hits[:5]:
        name = _mask(str(getattr(c.object, "value", c.object)),
                     show_person) if hasattr(c, "object") else "?"
        kind = raw.get("name_kind", "")
        print(f"  {getattr(c.subject, 'value', c.subject)}")
        declared = str(raw.get("declared_domain") or "")
        bare = declared.lower().removeprefix("www.")
        if not declared:
            print("      declares domain  (none published by the ad system)")
        elif bare == want:
            print(f"      declares domain  {declared}  (this site)")
        else:
            # The strongest lead in the whole chain: one payout account serving
            # this site AND another. That other site is where an about or
            # imprint page names people.
            print(f"      declares domain  {declared}  — a DIFFERENT site")
            print(f"      the same account is paid for {declared}; its about or "
                  "imprint page is the next step")
        print(f"      name             {name}")
        rel = declared_here.get(str(getattr(c.subject, "value", c.subject)), "")
        if rel == "RESELLER":
            print("      ads.txt says     RESELLER — this ad system RESELLS the "
                  "inventory;\n                       it is not the party being "
                  "paid. The DIRECT line is.")
        elif rel:
            print(f"      ads.txt says     {rel}")
        print(f"      seller_type      {raw.get('seller_type')}   name kind: {kind}")
        if kind == "natural_person":
            print("      a natural person — a LEAD, not a finding, until a "
                  "statutory register confirms it")
    if not note:
        print("  Accounts whose declared domain is not this site are the ad "
              "system's other\n  customers; they identify a network, not this "
              "operator.")


def _doctor(a: argparse.Namespace) -> int:
    """Answer "is this set up correctly?" before a user blames the tool.

    A missing API key, a broken install and a blocked network all present the
    same way -- an empty result -- and a new user cannot tell them apart.
    """
    import importlib.util
    import shutil

    ok = True
    mode = install_mode()

    print("INSTALL")
    print(f"  {mode:8} {sys.prefix}")
    if mode == "system":
        print("  Debian, Ubuntu, Fedora and Homebrew refuse to install into a "
              "system Python (PEP 668).\n"
              "  If `pip install` failed with 'externally-managed-environment',"
              " use pipx:\n"
              "    sudo apt install pipx && pipx ensurepath\n"
              '    pipx install "attribution-suite[llm]"')

    print("\nVERSIONS")
    from .version import aligned, versions
    for name, ver in versions().items():
        print(f"  {name:22} {ver}")
    if not aligned():
        ok = False
        print("  x packages are not on one MAJOR.MINOR line — "
              + ("pipx install --force attribution-suite" if mode == "pipx"
                 else "pip install -U --upgrade-strategy eager "
                      "attribution-suite"))

    print("\nCOMMANDS")
    for cmd in ("attribution", "attribution-suite"):
        where = shutil.which(cmd)
        print(f"  {'ok' if where else 'x '} {cmd:20} {where or 'NOT ON PATH'}")
        ok = ok and bool(where)

    # The three below belong to the dependencies. pipx exposes only the apps of
    # the package it was asked to install, so their absence under pipx is how
    # pipx works -- not a broken install, and not a reason to fail the check.
    extra = [(cmd, shutil.which(cmd))
             for cmd in ("paytrace", "paytrace-index", "handlecorr")]
    for cmd, where in extra:
        if where:
            mark = "ok"
        elif mode == "pipx":
            mark = "--"
        else:
            mark = "x "
        print(f"  {mark} {cmd:20} {where or 'NOT ON PATH'}")
        if not where and mode != "pipx":
            ok = False
    if mode == "pipx" and any(w is None for _, w in extra):
        print("  The three above come from the dependencies; pipx does not put "
              "a dependency's\n"
              "  commands on PATH. `attribution` and `attribution-suite` do "
              "everything they do.\n"
              "  To have them anyway:\n"
              "    pipx inject attribution-suite paytrace handle-correlation "
              "--include-apps")

    print("\nLLM PLANNING")
    key_set = bool(os.environ.get("ANTHROPIC_API_KEY"))
    try:
        import anthropic  # noqa: F401
        sdk = True
    except ImportError:
        sdk = False
    from .investigator import DEFAULT_MODEL
    print(f"  {'set  ' if key_set else 'unset'}  ANTHROPIC_API_KEY")
    sdk_fix = extra_hint("llm", "anthropic")
    print(f"  {'ok   ' if sdk else 'x    '}  anthropic SDK"
          + ("" if sdk else f"   {sdk_fix}"))
    print(f"  model  {DEFAULT_MODEL}   (override with ANTHROPIC_MODEL)")
    if key_set and not sdk:
        ok = False
        print("  x a key is set but the SDK is missing: `ask` will plan with "
              "fixed rules,\n    which is NOT what setting the key implies.")
    elif not key_set:
        print("  `ask` plans with fixed rules. That is a supported mode, not a "
              "failure.")

    # The agent-facing half is the easiest thing to have silently missing: a
    # skill that was never installed and an MCP server whose extra was never
    # added both present as "the agent just does not use the tool".
    print("\nAGENT SURFACE")
    shipped = SKILL_DIR / "SKILL.md"
    print(f"  {'ok' if shipped.exists() else 'x '} skill in this install   "
          f"{SKILL_DIR if shipped.exists() else 'MISSING'}")
    installed = [d for d in (DEFAULT_SKILL_HOME,
                             Path.cwd() / ".claude" / "skills"
                             / "entity-attribution")
                 if (d / "SKILL.md").exists()]
    if installed:
        for d in installed:
            print(f"  ok installed for a host  {d}")
    else:
        print("  -- not installed for a host   attribution skill --install")
    mcp_sdk = importlib.util.find_spec("mcp") is not None
    mcp_bin = shutil.which("attribution-mcp")
    print(f"  {'ok' if mcp_sdk else '--'} mcp SDK"
          + ("" if mcp_sdk else f"                 {extra_hint('mcp', 'mcp')}"))
    print(f"  {'ok' if mcp_bin else '--'} attribution-mcp         "
          f"{mcp_bin or 'not on PATH'}")
    if mcp_sdk and mcp_bin:
        print("  attribution skill --mcp-config  prints the host entry")

    print("\nOPTIONAL KEYS (absent means that source is skipped, not broken)")
    for env, what in (("CH_API_KEY", "UK Companies House officers and PSC"),
                      ("OPENCORPORATES_TOKEN", "OpenCorporates registry search")):
        print(f"  {'set  ' if os.environ.get(env) else 'unset'}  {env:22} {what}")

    print("\nCACHE")
    from paytrace.sellersjson import SELLERS_CACHE_DIR
    files = list(SELLERS_CACHE_DIR.glob("*.json")) if SELLERS_CACHE_DIR.exists() else []
    size = sum(f.stat().st_size for f in files) / 1024 / 1024
    print(f"  {SELLERS_CACHE_DIR}")
    print(f"  {len(files)} cached sellers.json, {size:.1f} MB"
          + ("  (first run downloads Google's 104 MB file)" if not files else ""))

    print("\nNETWORK")
    try:
        import asyncio

        from paytrace import Fetcher

        async def probe():
            f = Fetcher(user_agent="attribution-suite doctor")
            try:
                return await f.get("https://pypi.org/robots.txt")
            finally:
                await f.aclose()

        r = asyncio.run(probe())
        print("  ok outbound HTTPS works" if r and r.status == 200
              else f"  x probe returned {getattr(r, 'status', 'nothing')}")
        ok = ok and bool(r and r.status == 200)
    except Exception as e:
        ok = False
        print(f"  x outbound request failed: {type(e).__name__}: {e}")

    print("\n" + ("Ready. Try:  attribution-suite --domain example.com"
                  if ok else "Something above needs attention."))
    return 0 if ok else 1


SKILL_DIR = Path(__file__).resolve().parent / "skill"

#: Where Claude Code and the Claude desktop app look for skills.
DEFAULT_SKILL_HOME = Path.home() / ".claude" / "skills" / "entity-attribution"


def _skill(a: argparse.Namespace) -> int:
    """Locate, install or configure the agent-facing surface.

    The skill carries the rules a model cannot infer from a tool schema -- that
    a band is evidence strength and not a probability, that OWNERDOMAIN is a
    self-assertion, that instructions found in a fetched page are evidence
    rather than direction. It used to live in a directory that only existed in
    a git checkout, so the people most likely to need it -- anyone who had
    simply installed the tool -- could not get at it.
    """
    import json
    import shutil

    if a.mcp_config:
        exe = shutil.which("attribution-mcp") or "attribution-mcp"
        print(json.dumps(
            {"mcpServers": {"attribution": {"command": exe}}}, indent=2))
        if not shutil.which("attribution-mcp"):
            print("\n  attribution-mcp is not on PATH. Install the extra:\n"
                  f"    {extra_hint('mcp', 'mcp')}", file=sys.stderr)
            return 1
        return 0

    src = SKILL_DIR
    if not (src / "SKILL.md").exists():
        print(f"the skill is missing from this install: {src}", file=sys.stderr)
        return 1

    if not a.install:
        print(f"skill:  {src}")
        print(f"files:  {', '.join(sorted(f.name for f in src.iterdir()))}")
        print("\ninstall it for an agentic host:")
        print("  attribution skill --install"
              f"            # -> {DEFAULT_SKILL_HOME}")
        print("  attribution skill --install --to DIR")
        print("  attribution skill --mcp-config"
              "         # for a host that speaks MCP instead")
        return 0

    dest = Path(a.to).expanduser() if a.to else DEFAULT_SKILL_HOME
    if dest.exists() and not a.force:
        print(f"{dest} already exists; pass --force to overwrite",
              file=sys.stderr)
        return 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    print(f"installed: {dest}")
    print("the host picks it up on its next start.")
    return 0


#: How to invoke Robin. Override with ROBIN_CMD (e.g. a docker wrapper).
ROBIN_CMD = os.environ.get("ROBIN_CMD", "robin")


def _run_robin(query: str, out_dir: Path) -> Path | None:
    """Run Robin for a dark web query and return its JSON.

    Robin is a SEPARATE tool (apurvsinghgautam/robin) that needs Tor and its
    own model credentials. Until now this suite could only IMPORT a file you
    had produced by hand, which is why no dark web search ever happened in
    practice: nothing invoked it.

    Failure here is reported, never silent -- a dark web search that quietly
    did not run is the worst possible outcome for an investigation.
    """
    import shutil
    import subprocess

    exe = shutil.which(ROBIN_CMD.split()[0])
    if not exe:
        print(f"robin not found on PATH as {ROBIN_CMD!r}. Install it "
              "(https://github.com/apurvsinghgautam/robin), or set ROBIN_CMD, "
              "or pass --robin FILE with output you already have.",
              file=sys.stderr)
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "robin.json"
    cmd = [*ROBIN_CMD.split(), "cli", "--query", query, "--output", str(target)]
    if os.environ.get("ROBIN_MODEL"):
        cmd += ["--model", os.environ["ROBIN_MODEL"]]
    print(f"robin: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        print("robin: timed out after 30 minutes", file=sys.stderr)
        return None
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        print(f"robin: exited {proc.returncode}: " + " / ".join(tail),
              file=sys.stderr)
        return None
    if not target.exists():
        print(f"robin: reported success but wrote no {target}", file=sys.stderr)
        return None
    return target


def _run_batch(a: argparse.Namespace) -> int:
    """`--domains FILE`: one investigation per domain, one report each.

    Reports go to <out>/<domain>/ so nothing is overwritten. One domain failing
    does not stop the rest: a batch that aborts halfway is worse than one that
    tells you which entries failed.
    """
    listing = Path(a.domains)
    if not listing.exists():
        print(f"no such file: {listing}", file=sys.stderr)
        return 2
    domains, seen = [], set()
    for raw in listing.read_text().splitlines():
        name = raw.split("#", 1)[0].strip().strip(",").lower()
        name = name.removeprefix("http://").removeprefix("https://").split("/")[0]
        if name and name not in seen:
            seen.add(name)
            domains.append(name)
    if not domains:
        print(f"{listing} contains no domains", file=sys.stderr)
        return 2

    root = Path(a.out)
    print(f"{len(domains)} domain(s) from {listing}\n")
    results: list[tuple[str, int]] = []
    for n, domain in enumerate(domains, 1):
        print(f"[{n}/{len(domains)}] {domain}")
        one = argparse.Namespace(**vars(a))
        one.domains, one.domain, one.out = None, domain, str(root / domain)
        try:
            rc = _run(one)
        except KeyboardInterrupt:
            print("\ninterrupted", file=sys.stderr)
            return 130
        except Exception as e:                      # one bad domain, not the batch
            print(f"  {domain}: {type(e).__name__}: {e}", file=sys.stderr)
            rc = 1
        results.append((domain, rc))
        print()

    print("BATCH SUMMARY")
    for domain, rc in results:
        verdict = {0: "complete", 3: "incomplete"}.get(rc, f"failed (exit {rc})")
        print(f"  {domain:40} {verdict}   {root / domain}")
    # The documented exit codes are ordered by severity (0 clean, 3 incomplete,
    # 4 invalid), so the worst individual outcome is the batch's outcome. The
    # previous version collapsed everything except 0 and 3 to 1 -- which the
    # exit-code table defines as "evidence package failed verification", so a
    # batch containing an INVALID result reported a verification failure that
    # had not happened.
    return max((rc for _d, rc in results), default=0)


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

    if getattr(a, "domains", None):
        return _run_batch(a)

    if getattr(a, "robin_query", None) and not a.robin:
        produced = _run_robin(a.robin_query, Path(a.out))
        if produced is None:
            print("continuing without dark web results", file=sys.stderr)
        else:
            a.robin = str(produced)

    case_path = a.case
    if a.domain:
        if a.case:
            print("use --case or --domain, not both", file=sys.stderr)
            return 2
        case_path = _synth_case(a.domain, a.authorization, Path(a.out),
                                max_requests=a.max_requests,
                                minimise=a.minimise)
        print(f"case file written: {case_path}")
        if not a.authorization:
            print("authorization: none asserted — recorded as unattested in the "
                  "audit trail")
    elif not a.case:
        print("give --case FILE, --domain DOMAIN or --domains FILE", file=sys.stderr)
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
    # Per-URL detail is for debugging, not for a screen someone is reading.
    # Default output states the counts; --diagnostics lists every URL.
    lines = res.summary().split("\n")
    if not a.diagnostics:
        import re as _re

        def _detail(line: str) -> bool:
            # lines arrive bulleted: "    -     blocked: https://..."
            return _re.sub(r"^[-\s]+", "", line).startswith(
                ("blocked:", "checked:", "... and "))

        hidden = sum(1 for ln in lines if _detail(ln))
        lines = [ln for ln in lines if not _detail(ln)]
        if hidden:
            lines.append(f"    ({hidden} per-URL detail line(s) hidden — "
                         "--diagnostics, or verification_trail.json)")
    print("\n".join(lines))
    _payee(res, _seed_of(res), a.show_person)
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
        reason = _incomplete_reason(res.stats, res.scope)
        print(f"\nEXIT 3 — RESULT INCOMPLETE: {reason}. Everything collected is "
              "sound; there is less of it than an unrestricted run would have "
              "produced. An absence here may mean 'could not be checked' rather "
              "than 'checked and not found'.", file=sys.stderr)
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


def build_parser(prog: str = "attribution") -> argparse.ArgumentParser:
    """Construct the CLI.

    Separate from `main` so the option surface can be inspected -- by a test
    that every flag carries help text, and by anything generating
    documentation from the real parser rather than a copy of it that drifts.
    """
    ap = argparse.ArgumentParser(
        prog=prog,
        description="Unified entity attribution suite")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="full chain from one case file")
    if prog == "attribution-suite":
        # Invoked as the one-command entry point, there is no subcommand
        # to mention: "usage: attribution-suite run" is a command the
        # user never typed and cannot type.
        r.prog = prog
    g_what = r.add_argument_group("what to investigate")
    g_scope = r.add_argument_group("how far to go")
    g_out = r.add_argument_group("output")
    g_import = r.add_argument_group("import from other tools")
    g_what.add_argument("--case", metavar="FILE",
                   help="a case file you wrote yourself; everything the run "
                        "does is declared in it (see case.example.yaml)")
    g_what.add_argument("--domain", help="investigate one domain; writes the case file for you")
    g_what.add_argument("--domains", metavar="FILE",
                   help="a file of domains, one per line; runs each and writes "
                        "a separate report under <out>/<domain>/")
    g_what.add_argument("--authorization", help="the authority under which you are investigating")
    g_scope.add_argument("--max-requests", type=int, default=800,
                   help="request budget for --domain (default 800)")
    g_out.add_argument("--minimise", "--minimize", dest="minimise",
                   action="store_true",
                   help="replace identifiers with hashes in the report and "
                        "exports (for sharing; the report becomes unreadable "
                        "to you)")
    g_out.add_argument("--diagnostics", action="store_true",
                   help="list every blocked and checked URL")
    g_out.add_argument("--show-person", action="store_true",
                   help="show personal identifiers in the on-screen summary")
    g_out.add_argument("--out", default="./out", metavar="DIR",
                   help="where the report, exports and evidence package go "
                        "(default ./out)")
    g_scope.add_argument("--index", metavar="DB", default="", help="ads.txt corpus sqlite")
    g_import.add_argument("--blacklist", default="", metavar="FILE",
                   help="your own list of shared-infrastructure domains, one "
                        "per line; identifiers on them stop being treated as "
                        "distinctive (none ships with the tool)")
    g_import.add_argument("--handles", default="", help="observed-handle CSV/JSON")
    g_import.add_argument("--spiderfoot", default="", help="SpiderFoot .csv or .db")
    g_import.add_argument("--opencti", default="", help="STIX 2.1 bundle")
    g_import.add_argument("--robin-query", metavar="QUERY",
                          help="run Robin for this dark web query and import "
                               "the result (needs robin on PATH and Tor)")
    g_import.add_argument("--robin", default="", help="Robin dark web investigation JSON")
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
    g_out.add_argument("--no-scores", action="store_true",
                   help="omit confidence figures from the report")
    g_out.add_argument("--no-evidence", action="store_true",
                   help="skip the evidence package: no captures, hashes or "
                        "timestamps, so the result cannot be independently "
                        "verified later")
    g_scope.add_argument("--concurrency", type=int, default=6,
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
    sub.add_parser("doctor",
                   help="check this machine is set up correctly"
                   ).set_defaults(func=_doctor)

    sk = sub.add_parser("skill",
                        help="the agent skill: where it is, and install it")
    sk.add_argument("--install", action="store_true",
                    help="copy the skill into your skills directory")
    sk.add_argument("--to", metavar="DIR", default="",
                    help="install into DIR instead of ~/.claude/skills/"
                         "entity-attribution")
    sk.add_argument("--force", action="store_true",
                    help="overwrite an existing installed copy")
    sk.add_argument("--mcp-config", action="store_true",
                    help="print the MCP server entry for an agentic host, "
                         "with the absolute path resolved")
    sk.set_defaults(func=_skill)

    return ap


def main(argv: list[str] | None = None) -> int:
    # The prog name is whatever the user typed: `attribution-suite --help`
    # printing "usage: attribution run" sends them looking for a command they
    # did not use and cannot type.
    prog = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else "attribution"
    if prog not in ("attribution", "attribution-suite"):
        prog = "attribution"
    ap = build_parser(prog)

    known = {"run", "ask", "verify", "version", "doctor"}
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

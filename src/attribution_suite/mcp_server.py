"""MCP server exposing the attribution toolkit to an agentic host.

    pip install "attribution-suite[mcp]"
    attribution-mcp                       # stdio, for Claude Desktop / MCP hosts

Register in `claude_desktop_config.json`:

    {"mcpServers": {"attribution": {"command": "attribution-mcp"}}}

## What is deliberately NOT exposed

The tool surface an agent gets is narrower than the CLI, because an agentic host
is a different threat model: the *user* is not reviewing each call, and the
model choosing arguments has read untrusted web content.

- **No person-name search.** `Investigator.classify()` refuses name-keyed
  searches for natural persons before any I/O; that refusal is preserved here
  rather than re-implemented, and the tool simply has no such parameter.
- **No arbitrary URL fetch.** An agent with a general fetch tool plus this
  toolkit's credibility is a laundering path for someone else's SSRF. Fetches
  are constrained to the documented collection surface for a seed.
- **No egress/proxy configuration.** Credentials belong in a case file the
  operator wrote, not in arguments a model chose.
- **No filesystem paths from the model.** Output goes to a server-chosen
  temporary directory, returned as content.

## Why the results carry their own caveats

Every response embeds the calibration status and the evidence bands in the text
the model reads, because a model summarising a result will otherwise present
`STRONG_EVIDENCE` as certainty. The caveat has to be in the payload, not in
documentation the model never sees.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ._version import __version__ as _VERSION  # noqa: E402

# The protocol types are an optional extra, but the tool LOGIC must stay
# importable without them. Otherwise it cannot be tested -- which is exactly
# why the MCP tests asserted schema shape rather than executing anything, and
# why a tool reading nonexistent SuiteResult fields shipped green.
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool

    MCP_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised by the no-extra path
    MCP_AVAILABLE = False

    @dataclass
    class TextContent:
        type: str
        text: str

    @dataclass
    class Tool:
        name: str
        description: str
        inputSchema: dict  # noqa: N815 - matches the protocol field name

    class Server:
        """No-op registrar so the handler decorators still bind."""

        def __init__(self, name: str) -> None:
            self.name = name

        def list_tools(self):
            return lambda fn: fn

        def call_tool(self):
            return lambda fn: fn

    def stdio_server():  # pragma: no cover
        raise SystemExit("MCP extra not installed")

#: RFC 1123 label syntax plus a TLD. `"/" not in domain` accepted
#: `..\..\etc`, IP literals, userinfo and unicode confusables -- and the value
#: was then interpolated into YAML text, so a crafted argument could add case
#: keys. Agentic arguments are chosen after the model has read untrusted
#: content, so they are untrusted input.
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)"
    r"(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")


def _validate_domain(raw: object) -> str:
    text = str(raw or "").strip().lower().rstrip(".")
    if text.startswith("xn--") or "--" in text.split(".")[0][2:4]:
        # Punycode is legitimate but confusable; require the caller to be
        # explicit rather than resolving a homograph silently.
        raise ValueError("punycode domains must be supplied already decoded")
    # An IPv4 literal satisfies RFC 1123 label syntax, so the regex alone
    # accepts 127.0.0.1 and 169.254.169.254. The SSRF guard would refuse them
    # downstream, but a tool that accepts them has already told the model this
    # is a supported shape.
    if re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}", text) or ":" in text:
        raise ValueError(
            "IP literals are not accepted; supply a domain name. Collection "
            "is scoped to named hosts so the record says what was investigated.")
    if not _DOMAIN_RE.match(text):
        raise ValueError(
            f"{raw!r} is not a bare domain name. Expected something like "
            "example.com -- not a URL, an email address, an IP literal or a "
            "path.")
    return text


CALIBRATION_NOTE = (
    "IMPORTANT — read this before summarising the result. These bands describe "
    "EVIDENCE STRENGTH, not probability. The model is not calibrated: "
    "STRONG_EVIDENCE means the evidence is strong, NOT that the attribution is "
    "95% likely. Do not restate a band as a percentage or as certainty. Any "
    "conclusion about a natural person is a lead until a statutory registry "
    "corroborates it."
)

#: How long an MCP case directory is kept. Evidence accumulating indefinitely
#: in the system temp area on a long-running agent host is a privacy and disk
#: problem, and mkdtemp's 0700 bounds who can read it, not how long it lives.
MCP_RETENTION_HOURS = float(os.environ.get("ATTRIBUTION_MCP_RETENTION_HOURS", "24"))


def sweep_stale_cases(now: float | None = None) -> int:
    """Remove MCP case directories past the retention window.

    Runs at server start and after each investigation. A directory is kept if
    it carries a `.retain` marker, so an operator can preserve a case
    deliberately rather than by the sweep failing to notice it.
    """
    import shutil
    import time

    now = now if now is not None else time.time()
    cutoff = now - MCP_RETENTION_HOURS * 3600
    removed = 0
    for path in Path(tempfile.gettempdir()).glob("attribution-mcp-*"):
        if not path.is_dir() or (path / ".retain").exists():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


app = Server("attribution")


def _top_entity(result) -> dict:
    """The resolved entity, or an explicit statement that nothing resolved.

    Reports a conclusion only when one exists. Taking `max(entities, key=len)`
    over singleton clusters returned whatever identifier happened to sort first
    -- on a real run that produced
    `"conclusion": "seller_id:pubmatic.example/156423"` alongside
    `"band": "UNSUPPORTED"`, which an agent would relay as the operator's
    identity. A raw identifier key is not an attribution, and a conclusion
    paired with UNSUPPORTED is incoherent.

    An entity qualifies when it merged at least two identifiers and its
    strongest assessment is above the corroboration floor.
    """
    from attribution_graph.scoring import Band

    NOTHING = {
        "label": None,
        "resolved": False,
        "band": None,
        "groups": 0,
        "note": ("No entity resolved above the corroboration threshold. This "
                 "is an ordinary outcome, not an error: report that the "
                 "operator could not be established, and do not present any "
                 "identifier from the graph as the answer."),
    }

    entities = [e for e in getattr(result.graph, "entities", {}).values()
                if len(getattr(e, "identifiers", ())) > 1]
    if not entities:
        return NOTHING

    assessments = list((getattr(result.resolution, "assessments", {}) or {}).values())
    top = max(assessments, key=lambda a: a.log_odds, default=None)
    band = getattr(getattr(top, "band", None), "value", None)
    if band in (None, Band.WEAK.value, Band.UNSUPPORTED.value):
        return NOTHING

    best = max(entities, key=lambda e: len(e.identifiers))
    label = getattr(best, "best_label", None)
    # A bare identifier key is not a name. Reporting one as the conclusion
    # dresses an unresolved run as an answer.
    if not label or ":" in label:
        return NOTHING

    return {
        "label": label,
        "resolved": True,
        "band": band,
        "groups": getattr(top, "independent_groups", 0),
    }


def _wrap(payload: dict[str, Any]) -> list[TextContent]:
    payload["calibration"] = "unvalidated"
    payload["how_to_report"] = CALIBRATION_NOTE
    return [TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="attribute_domain",
            description=(
                "Investigate which legal entity operates a domain, using its "
                "advertising declarations (ads.txt, sellers.json), infrastructure "
                "and mandated disclosures. Returns an evidence-ranked result with "
                "provenance. Bands are evidence strength, NOT calibrated "
                "probability."),
            inputSchema={
                "type": "object",
                "properties": {
                    "domain": {"type": "string",
                               "description": "Domain to investigate, e.g. example.com"},
                    "authorization": {
                        "type": "string",
                        "description": (
                            "Why this investigation is authorised. Recorded in "
                            "the audit trail. Required: an investigation with no "
                            "stated basis is not one this tool performs.")},
                    "max_requests": {"type": "integer", "default": 40,
                                     "description": "Request budget. Enforced."},
                },
                "required": ["domain", "authorization"],
            },
        ),
        Tool(
            name="explain_ads_txt",
            description=(
                "Parse a domain's ads.txt and explain what it reveals: which "
                "accounts are discriminating versus boilerplate, and whether "
                "OWNERDOMAIN is a self-assertion. Read-only, single fetch."),
            inputSchema={
                "type": "object",
                "properties": {
                    "domain": {"type": "string"},
                    "authorization": {
                        "type": "string",
                        "description": (
                            "Why this retrieval is authorised. Required for "
                            "every tool that touches the network.")},
                },
                "required": ["domain", "authorization"],
            },
        ),
        Tool(
            name="correlate_handles",
            description=(
                "Score whether observed handles belong to one actor. Takes "
                "OBSERVED handles only — it does not search for a person by "
                "name, and will refuse input that looks like a name-keyed "
                "person search."),
            inputSchema={
                "type": "object",
                "properties": {
                    "observations": {
                        "type": "array",
                        "description": (
                            "Handles already observed, with platform and any "
                            "durable identifiers seen alongside them."),
                        "items": {
                            "type": "object",
                            "properties": {
                                "handle": {"type": "string"},
                                "platform": {"type": "string"},
                                "linked": {"type": "object"},
                            },
                            "required": ["handle", "platform"],
                        },
                    },
                },
                "required": ["observations"],
            },
        ),
        Tool(
            name="registry_coverage",
            description=(
                "What company registers exist for a jurisdiction, whether they "
                "are automatable, and what an absence from them does and does "
                "not mean. Answers offline from the shipped catalogue."),
            inputSchema={
                "type": "object",
                "properties": {"jurisdiction": {
                    "type": "string",
                    "description": "ISO code, e.g. GB, AE, SG"}},
                "required": ["jurisdiction"],
            },
        ),
    ]


#: Tools that perform live retrieval. Every one requires `authorization`, and
#: the check runs centrally rather than per handler -- enforcing it inside
#: `explain_ads_txt` and relying on `attribute_domain` to do the same left the
#: primary tool raising KeyError instead of refusing.
NETWORK_TOOLS = frozenset({"attribute_domain", "explain_ads_txt"})


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name in NETWORK_TOOLS and not str(
            (arguments or {}).get("authorization", "")).strip():
        return _wrap({
            "error": "authorization is required for any tool that performs "
                     "network retrieval. State why this investigation is "
                     "authorised; it is recorded in the audit trail.",
        })

    if name == "attribute_domain":
        return await _attribute_domain(arguments)
    if name == "explain_ads_txt":
        return await _explain_ads_txt(arguments)
    if name == "correlate_handles":
        return _correlate_handles(arguments)
    if name == "registry_coverage":
        return _registry_coverage(arguments)
    return _wrap({"error": f"unknown tool {name!r}"})


async def _attribute_domain(args: dict) -> list[TextContent]:
    from attribution_suite import run_case

    try:
        domain = _validate_domain(args.get("domain"))
    except ValueError as e:
        return _wrap({"error": str(e)})

    sweep_stale_cases()
    work = Path(tempfile.mkdtemp(prefix="attribution-mcp-"))
    case = work / "case.yaml"
    # Built as a mapping and serialised, never assembled as text. String
    # interpolation of a model-supplied value into YAML lets a crafted argument
    # introduce keys the caller never intended.
    case.write_text(yaml.safe_dump({
        "case_ref": f"MCP-{domain}",
        "authorization": str(args.get("authorization", "")).strip(),
        "seeds": [f"domain:{domain}"],
        "entity_types_allowed": ["Company"],
        "audit_path": str(work / "audit.jsonl"),
        "robots_policy": "respect",
        "minimize": True,
        "max_requests": max(1, min(int(args.get("max_requests", 40)), 500)),
    }, sort_keys=True))

    # Offloaded to a worker thread. `run_case()` is synchronous and calls
    # asyncio.run() internally; invoking it from an async handler while the MCP
    # server already owns a loop raises "asyncio.run() cannot be called from a
    # running event loop". The previous fix corrected the fields it read and
    # never exercised the real async invocation path.
    result = await asyncio.to_thread(run_case, str(case), work / "out")

    # SuiteResult exposes scope/graph/resolution/trail/evidence/outputs/
    # warnings/stats. It has no `conclusion` or `band`: reading them raised
    # AttributeError at runtime while the package suite stayed green, because
    # the MCP tests asserted schema shape rather than executing the tool.
    top = _top_entity(result)
    return _wrap({
        "domain": domain,
        "resolved": top["resolved"],
        "conclusion": top["label"],
        "band": top["band"],
        **({"note": top["note"]} if not top["resolved"] else {}),
        "independent_evidence_groups": top["groups"],
        "entities_resolved": len(getattr(result.graph, "entities", {}) or {}),
        "evidence_verified": result.stats.get("evidence_verified"),
        "result_valid": result.stats.get("result_valid", True),
        "result_complete": result.stats.get("result_complete", True),
        "fetches": result.stats.get("fetches"),
        "captures": result.stats.get("captures"),
        "warnings": result.warnings,
        "evidence_package": str(work / "out" / "evidence"),
        "retention": (
            f"this case directory is removed after {MCP_RETENTION_HOURS:.0f}h. "
            f"To keep it, create {work / '.retain'}. It contains the subject's "
            "retrieved content and is not made safe to share by minimisation."),
    })


async def _explain_ads_txt(args: dict) -> list[TextContent]:
    """Requires authorization: it performs live retrieval.

    This took only `domain`, so the safety control the agentic surface
    advertised applied to one of two network tools. A control that depends on
    which tool the model happened to pick is not structural.
    """
    from attribution_graph import CaseScope
    from paytrace import Fetcher, key_accounts, parse_ads_txt

    try:
        domain = _validate_domain(args.get("domain"))
    except ValueError as e:
        return _wrap({"error": str(e)})

    # A case-scoped audit trail. This tool performed live retrieval while
    # telling the caller "authorization is recorded in the audit trail" and
    # recorded nothing -- a governance gap, not a functional one, which is why
    # it survived several rounds of functional review.
    work = Path(tempfile.mkdtemp(prefix="attribution-mcp-"))
    (work / "case.yaml").write_text(yaml.safe_dump({
        "case_ref": f"MCP-adstxt-{domain}",
        "authorization": str(args["authorization"]).strip(),
        "seeds": [f"domain:{domain}"],
        "audit_path": str(work / "audit.jsonl"),
        "minimize": True,
    }, sort_keys=True))
    scope = CaseScope.load(str(work / "case.yaml"))
    scope.audit("mcp_explain_ads_txt", target=domain, tool="explain_ads_txt")
    fetcher = Fetcher(user_agent=f"attribution-mcp/{_VERSION} (+read-only)",
                      cache_dir=work / ".cache")
    try:
        r = await fetcher.get(f"https://{domain}/ads.txt")
    finally:
        await fetcher.aclose()

    scope.audit("mcp_explain_ads_txt_result", target=domain,
                status=getattr(r, "status", None),
                egress=fetcher.egress.get().label,
                blocked=len(fetcher.blocked))

    if not r or r.status != 200:
        # The audit trail belongs on this path too: a retrieval that failed
        # still happened, and "we looked and there was nothing" is exactly the
        # claim a reviewer needs evidence for.
        return _wrap({"domain": domain,
                      "ads_txt": "not published or unreachable",
                      "meaning": "absence is common and is not itself a finding",
                      "audit_trail": str(work / "audit.jsonl")})

    parsed = parse_ads_txt(r.text, domain)
    return _wrap({
        "domain": domain,
        "records": len(parsed.accounts),
        "direct": parsed.direct_count,
        "key_accounts": [str(a) for a in key_accounts(parsed)[:5]],
        "ownerdomain": parsed.variables.get("OWNERDOMAIN"),
        "audit_trail": str(work / "audit.jsonl"),
        "ownerdomain_caveat": (
            "OWNERDOMAIN is declared by the publisher about itself. It is a "
            "lead, never a finding: an operator can name any domain."),
    })


def _correlate_handles(args: dict) -> list[TextContent]:
    from handle_correlation import Observation, all_claims, correlation_points

    obs = [Observation(handle=o["handle"], platform=o["platform"],
                       linked=o.get("linked") or {})
           for o in args["observations"]]
    hc = correlation_points(all_claims(obs))
    return _wrap({
        "points": hc.points,
        "level": hc.level.value,
        "durable_identifier": hc.has_durable_identifier,
        "caveat": hc.caveat,
        "note": ("A correlation point must CONNECT two observations. Distinct "
                 "identifiers seen on one profile each do not corroborate."),
    })


def _registry_coverage(args: dict) -> list[TextContent]:
    from paytrace import federated_warning
    from paytrace.catalog import load_catalog

    j = str(args["jurisdiction"]).strip().upper()
    rows = [r for r in load_catalog()
            if r.jurisdiction == j or r.federation_of == j]
    return _wrap({
        "jurisdiction": j,
        "registers": [{"name": r.name, "automatable": r.automatable,
                       "access": r.access, "language": r.language,
                       "requires_local_egress": r.requires_local_egress,
                       "notes": r.notes} for r in rows],
        "federated_warning": federated_warning(j) or None,
    })


def main() -> None:
    if not MCP_AVAILABLE:
        raise SystemExit(
            "MCP support requires the optional extra:\n"
            "    pip install 'attribution-suite[mcp]'")

    import asyncio

    sweep_stale_cases()

    async def _serve():
        async with stdio_server() as (r, w):
            await app.run(r, w, app.create_initialization_options())

    asyncio.run(_serve())


if __name__ == "__main__":
    main()

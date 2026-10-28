"""Run the full chain with no network and nothing installed.

The skill was instructions pointing at a package from PyPI, and the chat
sandboxes it was written for will not install one: `pip install` is refused as
code from an external source, and the MCP config runs `uvx`, which hits the
same wall. So the skill reported "command not found" and the assistant fell
back to ad-hoc web lookups -- no bands, no correlation groups, no evidence
package, no audit record. Every property the toolkit exists to provide was
exactly what got dropped.

The fix splits the work along the line that actually divides those
environments. The assistant **can** reach the web; its *sandbox* cannot. So
the assistant fetches, and this runs the real pipeline over what it fetched.

**Nothing here re-implements the analysis.** `ReplayFetcher` satisfies the
same interface as the live `Fetcher` and serves saved documents instead of
making requests, so the real collectors, engine, scoring, resolution,
adversarial checks, gravatar linking and report all run unchanged. A second
implementation would drift from the first, and a divergent copy of a scoring
rule is worse than no copy -- this file has no copy of one.

The fetch plan is produced the same way: run the pipeline, record what the
collectors ask for and do not find, and print that. The plan is therefore
always exactly what the real collectors want, never a hand-maintained list
that goes stale.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: How a saved document is named: `<seq>-<kind>-<host>-<key>.<ext>`.
#:
#: The key is a digest of the full URL, and matching is done on it rather than
#: by reconstructing a URL from the readable part. Reconstruction was the
#: first design and it is lossy the moment a path is involved: Google's
#: sellers.json lives at `storage.googleapis.com/adx-rtb-dictionaries/
#: sellers.json`, which came back as `/sellers.json` and so never matched the
#: document that had been fetched for it. The seq number is cosmetic, so a
#: file keeps working if it is renumbered or fetched out of order.
NAME_RE = re.compile(r"^(?P<seq>\d+)-(?P<kind>[a-z0-9_]+)-(?P<host>.+?)"
                     r"-(?P<key>[0-9a-f]{8})\.(?:txt|json|html)$")

#: Documents whose completeness can be checked by parsing them.
STRUCTURED = {"sellers_json", "json"}


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(url: str) -> tuple[str, str, str]:
    """(kind, host, tail) for a URL, matching what `plan` will ask for."""
    m = re.match(r"https?://([^/]+)(/.*)?$", url)
    host, path = (m.group(1), m.group(2) or "/") if m else (url, "/")
    low = path.lower()
    if low.endswith("/ads.txt"):
        kind = "ads_txt"
    elif low.endswith("/app-ads.txt"):
        kind = "app_ads_txt"
    elif low.endswith("sellers.json"):
        kind = "sellers_json"
    elif low.endswith("/robots.txt"):
        kind = "robots_txt"
    elif low.endswith(".json") or "/api/" in low:
        kind = "json"
    else:
        kind = "page"
    tail = re.sub(r"[^a-z0-9]+", "-", path.strip("/").lower()).strip("-")
    return kind, host, tail


def url_key(url: str) -> str:
    """Stable, exact identity for a URL. Eight hex characters is plenty to
    distinguish the handful of documents in one case, and short enough to keep
    the filename readable."""
    return hashlib.sha256(url.rstrip("/").encode()).hexdigest()[:8]


def filename_for(url: str, seq: int) -> str:
    kind, host, _tail = _slug(url)
    ext = "json" if kind in STRUCTURED else "txt"
    host = re.sub(r"[^A-Za-z0-9.-]+", "-", host)[:40].strip("-")
    return f"{seq:02d}-{kind}-{host}-{url_key(url)}.{ext}"


@dataclass
class Wanted:
    """A URL a collector asked for and did not find in the corpus."""
    url: str
    collector: str = ""
    why: str = ""


@dataclass
class Corpus:
    """Documents the assistant fetched, keyed by a digest of their URL."""
    documents: dict[str, dict] = field(default_factory=dict)
    #: Files in the directory that are not named the way the plan asked.
    #: Reported rather than ignored: a document saved under the wrong name is
    #: invisible, and an investigation silently missing its ads.txt is worse
    #: than one that says a file could not be matched.
    unmatched: list[str] = field(default_factory=list)

    @classmethod
    def from_dir(cls, directory: str | Path) -> Corpus:
        """Load saved documents, recovering each URL from its filename."""
        d = Path(directory)
        if not d.is_dir():
            raise SystemExit(f"no such directory: {d}")
        corpus = cls()
        for p in sorted(d.iterdir()):
            if not p.is_file() or p.name.startswith("."):
                continue
            if p.name == "index.json":
                # An explicit index wins: it carries the exact URLs, which a
                # filename can only approximate once a path gets long.
                for row in json.loads(p.read_text()).get("documents", []):
                    body = (d / row["file"]).read_text(errors="replace")
                    corpus.add(row["url"], body, status=row.get("status", 200),
                               retrieved_at=row.get("retrieved_at", ""))
                return corpus
            m = NAME_RE.match(p.name)
            if not m:
                corpus.unmatched.append(p.name)
                continue
            corpus.add_by_key(m.group("key"), p.read_text(errors="replace"),
                              kind=m.group("kind"), name=p.name)
        return corpus

    def add_by_key(self, key: str, body: str, *, kind: str = "",
                   name: str = "") -> None:
        """A document whose URL is not known yet; it is matched on lookup."""
        self.documents[key] = {
            "url": "", "kind": kind, "body": body, "status": 200,
            "sha256": hashlib.sha256(body.encode("utf-8", "replace")).hexdigest(),
            "bytes": len(body.encode("utf-8", "replace")),
            "retrieved_at": "", "truncated": self._looks_truncated(kind, body),
            "rejected": self._wrong_document(kind, body),
            "file": name,
        }

    def add(self, url: str, body: str, *, status: int = 200,
            retrieved_at: str = "") -> None:
        kind, _host, _tail = _slug(url)
        truncated = self._looks_truncated(kind, body)
        self.documents[url_key(url)] = {
            "url": url, "kind": kind, "body": body, "status": status,
            "sha256": hashlib.sha256(body.encode("utf-8", "replace")).hexdigest(),
            "bytes": len(body.encode("utf-8", "replace")),
            "retrieved_at": retrieved_at or _utc(),
            "truncated": truncated,
            "rejected": self._wrong_document(kind, body),
        }

    @staticmethod
    def _wrong_document(kind: str, body: str) -> str:
        """Why this file is not the document it is named after, or "".

        The case that forced this: asked for `weather.com/ads.txt` verbatim, an
        assistant's fetch tool answered "I can't reproduce the full file" and
        returned a structural summary instead. Saved under the ads.txt name it
        parsed to zero records -- and the report then said "a site with no
        advertising declarations publishes no payee" about a site with about
        185 of them. A confident absence drawn from a document that was never
        read is the one inversion this toolkit exists to prevent, and the
        instruction not to save a summary is not a control: it is a request.

        The test is structural, not a guess about intent. Every non-comment,
        non-blank line of a real ads.txt parses as a record or a variable, and
        a real sellers.json parses as JSON or is a cut-off prefix of one. An
        empty or comments-only ads.txt is legitimate and must pass.
        """
        if not body.strip():
            return ""                      # legitimately empty
        if kind in {"ads_txt", "app_ads_txt"}:
            from paytrace.adstxt import parse

            meaningful = [ln for ln in body.splitlines()
                          if ln.strip() and not ln.lstrip().startswith("#")]
            if not meaningful:
                return ""                  # comments only: legitimate
            ads = parse(body, "")
            if not ads.accounts and not ads.variables:
                return ("none of its lines parse as an ads.txt record or "
                        "variable, so this is not the file the site served "
                        "— most often a fetch tool's summary or an error page "
                        "saved in its place")
            return ""
        if kind in STRUCTURED and body.lstrip()[:1] not in {"{", "["}:
            return ("it does not begin as JSON, so this is not the "
                    "sellers.json the ad system served — most often a "
                    "fetch tool's summary or an error page")
        return ""

    @staticmethod
    def _looks_truncated(kind: str, body: str) -> bool:
        """Detect a cut-off document rather than trusting it to be whole.

        This is the property that matters most in this mode. An assistant's
        browsing tool silently truncates large responses -- which is exactly
        what happened to the sellers.json files in the run that prompted all
        this -- and a truncated sellers.json parsed as "the seller is not in
        here" turns *not checked* into *checked and absent*. That inversion is
        the one thing this toolkit exists to prevent, so it is detected, not
        declared: a structured document that does not parse is incomplete.
        """
        if kind not in STRUCTURED:
            return False
        try:
            json.loads(body)
        except (ValueError, TypeError):
            return True
        return False

    def get(self, url: str) -> dict | None:
        doc = self.documents.get(url_key(url))
        if doc is not None and not doc["url"]:
            # First time this document has been asked for: now its URL is
            # known, so record it for the integrity table.
            doc["url"] = url
            doc["kind"] = doc["kind"] or _slug(url)[0]
            doc["truncated"] = self._looks_truncated(doc["kind"], doc["body"])
            doc["rejected"] = self._wrong_document(doc["kind"], doc["body"])
        return doc


class _Response:
    """What the live fetcher returns. Same attributes, no network."""

    __slots__ = ("url", "status", "text", "from_cache")

    def __init__(self, url: str, status: int, text: str) -> None:
        self.url, self.status, self.text, self.from_cache = url, status, text, True

    def json(self) -> Any:
        return json.loads(self.text)


class ReplayFetcher:
    """The live fetcher's interface, served from saved documents.

    Every miss is recorded rather than raised: a run over an empty corpus
    therefore completes and yields the full list of what the collectors wanted,
    which is the fetch plan.
    """

    def __init__(self, corpus: Corpus) -> None:
        self.corpus = corpus
        self.wanted: list[Wanted] = []
        self._seen_wanted: set[str] = set()
        self.blocked: list[tuple[str, str]] = []
        self.blocked_by_collector: dict[str, int] = {}
        self.checked_absent: list[tuple[str, str]] = []
        self.truncated_reads: list[tuple[str, str]] = []
        #: Saved files that are not the document they are named after.
        self.rejected: list[tuple[str, str]] = []
        self.count = 0
        self.tls_intercepted = ""
        # Only the attributes something actually reads off a fetcher.
        # `policy` and `evidence_log` were here as interface padding and
        # nothing in four repositories reads either one from a fetcher
        # object -- an invariant test flagged them as exactly the
        # assigned-and-never-read shape that every wiring defect here has had.
        self.policy_decisions: list = []
        from paytrace.egress import Egress, EgressPool
        self.egress = EgressPool(
            [Egress(label="assistant", provider="environment",
                    note="retrieved by the assistant's own browsing tool")],
            "assistant")

    # ---- the interface collectors use ------------------------------------ #

    async def get(self, url: str, headers: dict | None = None,
                  allow_html: bool = False, egress: str = "",
                  scanner=None, scan_max_bytes: int | None = None):
        doc = self.corpus.get(url)
        if doc is None:
            self._want(url)
            return None
        if doc["rejected"]:
            # Not served at all. Parsing it would produce zero records and the
            # run would report a confident absence for a document nobody
            # read. Recorded as a block, which is what it is: this retrieval
            # produced no usable evidence.
            self.note_block(url, f"the saved file was refused: {doc['rejected']}")
            self.rejected.append((url, doc["rejected"]))
            return None
        self.count += 1
        if doc["truncated"]:
            # Returned, because a partial document still yields real records,
            # but recorded so an absence found in it is never reported as
            # "checked and not found".
            self.truncated_reads.append(
                (url, f"the saved copy is incomplete ({doc['bytes']} bytes "
                      "and it does not parse) — the fetch was cut short"))
        if scanner is not None:
            # Scan mode: the live fetcher streams and hands chunks over. Here
            # the whole saved body is one chunk.
            scanner(doc["body"].encode("utf-8", "replace"))
        return _Response(url, doc["status"], doc["body"])

    async def get_text(self, url: str, **kw):
        r = await self.get(url, **kw)
        return r.text if r is not None else None

    async def get_json(self, url: str, **kw):
        r = await self.get(url, **kw)
        if r is None:
            return None
        try:
            return r.json()
        except ValueError:
            self.note_block(url, "the saved copy is not valid JSON "
                                 "(usually a truncated fetch)")
            return None

    async def post_form(self, url: str, data: dict | None = None, **kw):
        # A form POST cannot be replayed from a saved GET, and pretending
        # otherwise would silently drop a source. Recorded as not done.
        self.note_block(url, "needs a form POST, which this offline mode "
                             "cannot replay")
        return None

    def note_block(self, url: str, reason: str) -> None:
        self.blocked.append((url, reason))
        try:
            from paytrace.net import CURRENT_COLLECTOR
            who = CURRENT_COLLECTOR.get() or "unknown"
        except Exception:                       # noqa: BLE001
            who = "unknown"
        self.blocked_by_collector[who] = self.blocked_by_collector.get(who, 0) + 1

    async def aclose(self) -> None:
        return None

    # ---- plan ------------------------------------------------------------- #

    def _want(self, url: str) -> None:
        key = url.rstrip("/")
        if key in self._seen_wanted:
            return
        self._seen_wanted.add(key)
        try:
            from paytrace.net import CURRENT_COLLECTOR
            who = CURRENT_COLLECTOR.get() or ""
        except Exception:                       # noqa: BLE001
            who = ""
        self.wanted.append(Wanted(url=url, collector=who))


# ---- driving the real pipeline -------------------------------------------- #

def build_scope(domain: str, authorization: str, *, case_ref: str = "",
                minimize: bool = False, audit_path: str = ""):
    """A CaseScope built in Python, so no YAML file and no PyYAML are needed.

    `CaseScope.load` imports PyYAML lazily for exactly this reason: a sandbox
    that cannot install anything can still build a scope.
    """
    from attribution_graph import CaseScope

    ref = case_ref or f"{domain}-{datetime.now(timezone.utc):%Y%m%d}"
    # An audit trail is written even here. "Your ticket numbers and reasons
    # are only in this chat" was one of the real costs of running without the
    # toolkit, and it costs one append-only file to fix.
    return CaseScope(
        case_ref=ref,
        authorization=authorization,
        seeds=[f"domain:{domain}"],
        minimize=minimize,
        audit_path=audit_path or str(Path.cwd() / f"{ref}-audit.jsonl"),
    )


async def _collect(corpus: Corpus, scope, *, adversarial: bool = True):
    """Run the real engine against the corpus. No network, no re-implementation."""
    from attribution_graph import Engine
    from paytrace.collectors import build_all

    fetcher = ReplayFetcher(corpus)
    collectors = build_all(fetcher, scope)
    engine = Engine(scope, collectors=collectors, concurrency=1)
    graph = engine.graph

    link_report = None

    async def _before_resolve(g) -> None:
        nonlocal link_report
        from paytrace.gravatar_link import link_gravatars
        link_report = link_gravatars(g)
        for claim in link_report.claims:
            g.add_claim(claim)

    try:
        result = await engine.run(before_resolve=_before_resolve)
    except TypeError:
        # An older attribution-graph without the hook. Link afterwards rather
        # than failing: the binding is still correct, it simply cannot be
        # demoted by checks that have already run.
        result = await engine.run()
        await _before_resolve(graph)
    _ = adversarial
    return graph, result, fetcher, link_report


#: Paths this mode will not ask an assistant to fetch unless told to.
#:
#: The toolkit probes these under an authorised case, rate-limited, with the
#: authorisation in an audit trail and the result recorded whether or not
#: anything was found. An assistant fetching them from a chat is a different
#: act: unauthenticated probing of someone else's server for exposed secrets,
#: carrying the user's address, with no case behind it. Several of them also
#: look exactly like an attack in the target's logs.
#:
#: Sourced from the toolkit's own list, so adding a path there covers it here.
def _sensitive_paths() -> set[str]:
    try:
        from paytrace.extract import SENSITIVE_PATHS
        return set(SENSITIVE_PATHS)
    except Exception:                           # noqa: BLE001
        return set()


def _is_sensitive(url: str) -> bool:
    paths = _sensitive_paths()
    low = url.lower()
    return any(low.endswith(p.lower()) for p in paths)


#: Below this rank a document is needed before a report is worth producing;
#: at or above it the document only enriches one.
ESSENTIAL_RANK = 2


def _is_fetchable(url: str) -> bool:
    """Whether it is reasonable to ask an assistant to fetch this at all.

    A collector may point at a service the operator self-hosts -- Yente
    defaults to `http://localhost:8000` -- and a private or loopback address
    means nothing in the assistant's environment. Asking for it produces a
    confusing failure at best; at worst the assistant fetches something on
    whatever machine it is running on.
    """
    m = re.match(r"https?://(\[[^\]]+\]|[^/:]+)", url or "")
    if not m:
        return False
    host = m.group(1).lower().strip("[]")

    # Address literals are judged by what the address IS, not by matching
    # strings. The first version carried a denylist of literals plus a regex
    # for RFC1918 -- which missed IPv6 entirely, missed carrier-grade NAT, and
    # tripped a scanner that reads a bare "0.0.0.0" anywhere as a listener
    # binding to every interface. `ipaddress` already knows all of this.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        # One predicate, not an enumeration. Listing the individual properties
        # still missed carrier-grade NAT (100.64.0.0/10), which is exactly the
        # kind of gap a hand-maintained list grows; `is_global` is false for
        # every range that is not publicly routable, including that one.
        return bool(ip.is_global) and not ip.is_multicast

    if host == "localhost" or host.endswith(
            (".local", ".internal", ".localdomain", ".localhost")):
        return False
    return "." in host


def _rank(url: str) -> tuple[int, str]:
    """Essential first, corroborating next. The chain before the colour."""
    kind, host, _tail = _slug(url)
    if kind in {"ads_txt", "app_ads_txt"}:
        return 0, url
    if kind == "sellers_json":
        return 1, url
    if kind == "robots_txt":
        return 2, url
    if any(h in host for h in ("rdap", "gleif", "sec.gov", "opencorporates",
                               "company-information", "crt.sh", "web.archive")):
        return 3, url
    return 4, url


def _render_plan(fetcher: ReplayFetcher, corpus: Corpus, start: int = 1,
                 include_sensitive: bool = False,
                 held_back: list[Wanted] | None = None) -> list[str]:
    """What to fetch next, with the filename to save each under.

    `held_back` is passed in rather than derived here. Deriving it from
    `fetcher.wanted` meant it was computed after the list had already been
    narrowed to the blocking documents -- which never contain a sensitive
    path -- so the notice explaining what was left out, and why, never
    appeared.
    """
    held_back = held_back or []
    if not fetcher.wanted and not held_back:
        return []
    fetcher.wanted = [w for w in fetcher.wanted if _is_fetchable(w.url)]
    if not include_sensitive:
        fetcher.wanted = [w for w in fetcher.wanted if not _is_sensitive(w.url)]
    fetcher.wanted.sort(key=lambda w: _rank(w.url))
    out = [
        "## Fetch these, then run again",
        "",
        "Retrieve each URL with your own browsing or web-fetch tool and save "
        "the response body **verbatim** under the given filename, in one "
        "directory. Do not summarise, reformat or re-key anything: the digest "
        "recorded for each document is taken over the bytes you save, and a "
        "tidied copy is no longer the document the site served.",
        "",
    ]
    for i, w in enumerate(fetcher.wanted, start=start):
        name = filename_for(w.url, i)
        who = f"  (wanted by: {w.collector})" if w.collector else ""
        out.append(f"{i:2d}. {w.url}")
        out.append(f"    save as: {name}{who}")
    if held_back and not include_sensitive:
        out += [
            "",
            f"**{len(held_back)} path(s) were left out of this plan on "
            "purpose.** The toolkit probes them under an authorised case, "
            "rate-limited and recorded; an assistant fetching them from a "
            "chat is unauthenticated probing of someone else's server for "
            "exposed secrets, carrying the user's own address and nothing to "
            "answer for it. Several of them read as an attack in the target's "
            "logs. Ask for them explicitly only if the authorisation covers "
            "it: " + ", ".join(sorted({_slug(w.url)[2] or "/"
                                       for w in held_back})[:6]),
        ]
    out += [
        "",
        f"{len(fetcher.wanted)} document(s). If a fetch fails or returns "
        "nothing, **skip the file** — a missing document is reported as 'not "
        "checked', which is correct. Never save a placeholder, an error page "
        "or a summary in its place: that would be recorded as what the site "
        "served.",
        "",
        "If your tool cuts a large response short, save what you got anyway. "
        "A document that does not parse is detected as incomplete and any "
        "absence in it is reported as 'not checked' rather than 'not found'.",
    ]
    return out


def run(domain: str, authorization: str, directory: str | Path | None = None,
        *, minimize: bool = False, show_scores: bool = True,
        include_sensitive: bool = False) -> tuple[str, int]:
    """The whole chain over saved documents. Returns (text, exit code)."""
    from attribution_graph import EntityType
    from attribution_graph.export import report

    corpus = Corpus.from_dir(directory) if directory else Corpus()
    scope = build_scope(domain, authorization, minimize=minimize)
    graph, result, fetcher, link_report = asyncio.run(_collect(corpus, scope))

    # Stop when the payment chain is complete, not when every corroborating
    # source has been exhausted. There is always another register to try, so
    # waiting for "nothing left to want" never terminates -- the first draft
    # of this looped forever asking for archive snapshots. What remains is
    # offered as optional enrichment instead, which is a decision for whoever
    # is reading, not a precondition for reading anything.
    fetchable = [w for w in fetcher.wanted if _is_fetchable(w.url)]
    held_back = [] if include_sensitive else [w for w in fetchable
                                              if _is_sensitive(w.url)]
    pending = [w for w in fetchable
               if include_sensitive or not _is_sensitive(w.url)]
    blocking = [w for w in pending if _rank(w.url)[0] < ESSENTIAL_RANK]

    if blocking:
        fetcher.wanted = blocking
        head = [
            f"# {scope.case_ref} — more documents needed",
            "",
            f"{len(corpus.documents)} document(s) read, "
            f"{len(graph.claims)} claim(s) so far. The payment chain is not "
            "complete yet.",
            "",
        ]
        plan = _render_plan(fetcher, corpus,
                            start=len(corpus.documents) + 1,
                            include_sensitive=include_sensitive,
                            held_back=held_back)
        if plan:
            return "\n".join(head + plan), 3

    allowed = set(scope.entity_types_allowed) or {EntityType.COMPANY}
    result = result if result is not None else None
    status: dict[str, Any] = {
        "result_valid": True,
        "result_complete": not (fetcher.blocked or fetcher.truncated_reads),
        "offline_replay": True,
    }
    if fetcher.rejected:
        # Not merely incomplete. A document was supplied that is not the
        # document, so the evidence set is missing pieces in a way nothing
        # here can bound -- which is what `result_valid: false` means, and it
        # stops the summary reporting an absence as a finding.
        status["result_valid"] = False
        status["documents_rejected"] = len(fetcher.rejected)
    if link_report is not None and link_report.matched:
        status["gravatar_links"] = len(link_report.matched)
    _ = allowed

    optional = [w for w in pending if _rank(w.url)[0] >= ESSENTIAL_RANK]
    if optional:
        status["result_complete"] = False

    body = report(graph, result, scope, show_scores=show_scores, status=status)
    notes = _integrity_notes(corpus, fetcher)
    tail = _optional_section(optional, len(corpus.documents))
    return "\n".join([*notes, "", body, *tail]), 0


def _optional_section(optional: list[Wanted], seq: int) -> list[str]:
    """Sources that would corroborate what is above, if you want them."""
    if not optional:
        return []
    out = [
        "",
        "## Corroboration still available",
        "",
        "The payment chain above is complete. These sources were not read, "
        "so **every absence in this report means 'not checked'** rather than "
        "'checked and not found'. Fetch any of them and run again to fold "
        "them in; a second independent source is what moves a link out of the "
        "single-source band.",
        "",
    ]
    for i, w in enumerate(optional[:14], start=seq + 1):
        out.append(f"{i:2d}. {w.url}")
        out.append(f"    save as: {filename_for(w.url, i)}")
    if len(optional) > 14:
        out.append(f"    ... and {len(optional) - 14} more")
    return out


def _integrity_notes(corpus: Corpus, fetcher: ReplayFetcher) -> list[str]:
    """What this run is and is not, stated before the findings.

    A report produced this way must not be mistaken for one from a live run.
    The retrievals were someone else's, the digests commit to what was saved
    rather than to what the origin served, and no vantage point was recorded.
    """
    used = [d for d in corpus.documents.values() if d["url"]]
    unused = [d for d in corpus.documents.values() if not d["url"]]
    cut = [d for d in used if d["truncated"]]
    out = [
        "> ## :: OFFLINE REPLAY — retrievals were not made by this toolkit ::",
        ">",
        "> Every document below was fetched by an assistant's browsing tool "
        "and saved by hand. The digests commit to **what was saved**, not to "
        "what the origin served: there is no independent record that the "
        "bytes are unaltered, no vantage point, and no robots.txt decision of "
        "this toolkit's own. Treat it as a reading of supplied documents. For "
        "a result someone else must be able to verify, re-run the installed "
        "toolkit, which retrieves and hashes its own evidence.",
        "",
        "## Documents read",
        "",
        "| Document | Bytes | SHA-256 | Usable |",
        "|---|---|---|---|",
    ]
    for d in sorted(used, key=lambda x: x["url"]):
        if d["rejected"]:
            ok = "**no — not the document**"
        elif d["truncated"]:
            ok = "partial — cut short"
        else:
            ok = "yes"
        out.append(f"| `{d['url']}` | {d['bytes']} | `{d['sha256'][:16]}…` | {ok} |")
    bad = [d for d in used if d["rejected"]]
    if bad:
        out += [
            "",
            f"## :: {len(bad)} SAVED FILE(S) ARE NOT THE DOCUMENT ::",
            "",
            "These were refused rather than parsed. A file that does not "
            "parse as what it is named yields no records, and a run that "
            "parsed it would report a confident absence for a document "
            "nobody read — the one inversion this toolkit exists to prevent. "
            "The usual cause is a fetch tool that summarises instead of "
            "returning bytes, or an error page saved in place of the file.",
            "",
        ]
        for d in bad:
            out.append(f"- `{d['url']}` — {d['rejected']}")
        out += [
            "",
            "**Fetch these again and save the response body byte for byte.** "
            "If your tool will not return a large file verbatim, ask it for "
            "the specific lines you need and run the installed toolkit for the "
            "rest; do not paste a description of the file.",
        ]
    if cut:
        out += [
            "",
            f"**{len(cut)} document(s) were cut short.** An absence in one of "
            "those means *not checked*, never *checked and not found*. The "
            "most common cause is a browsing tool truncating a large "
            "sellers.json; fetching that URL directly, or asking for the "
            "specific seller ID, usually gets the record.",
        ]
    if unused or corpus.unmatched:
        names = [d.get("file", "?") for d in unused] + corpus.unmatched
        out += [
            "",
            f"**{len(names)} saved file(s) were not used.** Either the "
            "filename was changed and the document is therefore invisible, or "
            "the chain stopped needing it once more context arrived — a "
            "RESELLER line describes a supply path rather than ownership, so "
            "its sellers.json is not chased once the DIRECT account resolves. "
            "Named rather than silently skipped, because the first case is a "
            "document missing from the investigation and looks identical to "
            "the second from here: "
            + ", ".join(f"`{n}`" for n in sorted(names)[:8]),
        ]
    # Deduplicated: a collector that asks for the same unreplayable source
    # once per candidate domain produced the same line several times, which
    # reads as several distinct failures.
    seen: set[tuple[str, str]] = set()
    unique = [b for b in fetcher.blocked
              if not (b in seen or seen.add(b))]      # type: ignore[func-returns-value]
    if unique:
        out += ["", f"**{len(unique)} source(s) could not be replayed.** These "
                    "are sources this mode cannot reach at all, not sources "
                    "that were checked and found nothing:"]
        out += [f"- `{u}` — {why}" for u, why in unique[:6]]
        if len(unique) > 6:
            out.append(f"- ... and {len(unique) - 6} more")
    return out


def main(argv: list[str] | None = None) -> int:
    """Entry point for the single-file bundle and for `python -m`."""
    import argparse

    ap = argparse.ArgumentParser(
        prog="attribution-portable",
        description="Run the attribution chain over documents you fetched, "
                    "with no network and nothing installed.",
        epilog="Typical use: run it once with no documents to get the fetch "
               "plan, retrieve those URLs with your own browsing tool, save "
               "them under the given names, and run it again.")
    ap.add_argument("--version", action="store_true",
                    help="print the version this bundle was built from")
    ap.add_argument("domain", nargs="?",
                    help="the site being investigated")
    ap.add_argument("--authorization", "-a",
                    help="why this investigation is authorised. Recorded in "
                         "the audit trail; a run without one is unattested")
    ap.add_argument("--dir", "-d", default="fetched",
                    help="directory of saved documents (default: ./fetched)")
    ap.add_argument("--minimise", "--minimize", action="store_true",
                    dest="minimise", help="mask identifiers in the report")
    ap.add_argument("--no-scores", action="store_true",
                    help="omit confidence figures")
    ap.add_argument("--include-sensitive", action="store_true",
                    help="also ask for paths like /.env and /.git/config. "
                         "Unauthenticated probing of someone else's server: "
                         "only with authorisation that covers it")
    ap.add_argument("--out", "-o", default="",
                    help="also write the report to this file")
    a = ap.parse_args(argv)

    if a.version:
        try:
            from attribution_suite import __version__ as v
        except Exception:                        # noqa: BLE001
            v = "unknown"
        print(f"attribution-portable {v} (offline bundle)")
        return 0
    if not a.domain:
        ap.error("a domain is required")
    if not a.authorization:
        ap.error("--authorization is required: a run without a stated reason "
                 "records itself as unattested")

    directory = Path(a.dir)
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)

    text, code = run(a.domain, a.authorization, directory,
                     minimize=a.minimise, show_scores=not a.no_scores,
                     include_sensitive=a.include_sensitive)
    print(text)
    if a.out:
        Path(a.out).write_text(text)
        print(f"\n[written to {a.out}]")
    return code


if __name__ == "__main__":                       # pragma: no cover
    raise SystemExit(main())

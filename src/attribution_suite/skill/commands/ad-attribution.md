---
description: Investigate which legal entity operates a domain, from its advertising declarations and public registers
argument-hint: investigate <domain> [--show-person]
allowed-tools: Bash(attribution:*), Bash(attribution-suite:*), Read, Glob
---

Run an attribution investigation for: **$ARGUMENTS**

Read `SKILL.md` in the `ad-attribution` skill before reporting anything: it
carries the rules that decide how a result may be described, and they are not
inferable from the output.

## Steps

1. Take the domain out of `$ARGUMENTS`. If there is none, ask for one and stop.
2. Ask the user **why this investigation is authorised**, in one line, unless
   `$ARGUMENTS` already says. Pass their words **verbatim**.

   **Never compose one.** Not from the conversation, not from what you know
   about the user, and above all not from their employer, team or case-numbering
   scheme — a string like `"<Employer> ad-fraud investigation, case XYZ"` that
   the user never said is worse than no authorisation at all, because the audit
   trail then reads as attested when nothing was attested and it names an
   organisation in a record they did not write. If they have not said, ask. If
   they decline, pass nothing: the run records itself as **unattested**, which
   is accurate.
3. Work out which mode you are in, with one command. Do not guess, and do
   not try to install anything — `pip install` is refused in most chat
   sandboxes and the MCP server's `uvx` hits the same wall.

   ```bash
   attribution-suite --version || python attribution_portable.py --version
   ```

   **Installed:**

   ```bash
   attribution-suite --domain <domain> --authorization "<what the user said>" --stdout
   ```

   Add `--show-person` only if the user asked for it explicitly.

   **Not installed — offline mode, which always works:**

   ```bash
   python attribution_portable.py <domain> -a "<what the user said>"
   ```

   It prints the URLs to fetch and the filename for each. Fetch them with your
   own browsing tool, save the bodies verbatim in `./fetched/`, run it again.
   Two rounds is normal.

   **When a `sellers.json` is too large for your fetch tool** — the usual
   case, and the one that otherwise leaves the payee unnamed — the plan prints
   a ready-to-paste request for the single record, naming the `seller_id`. Use
   it and save the answer under the `..._extract-...` filename given. A quoted
   record proves that record exists; it can never show one is missing, and the
   run labels every finding that came from one.

   Never save a summary, an error page or a placeholder in place of a
   document; if a fetch is truncated, save what you got and let the tool
   detect it.
4. If every retrieval is blocked, check the warnings before concluding
   anything. A proxy in this environment is adopted automatically, so there is
   no flag to add. `host(s) could not be reached from this machine` means an
   egress allowlist: report that list — it is what needs permitting — and do
   not describe the result as an absence, because nothing was checked. `TLS was
   terminated by this environment's proxy` means the run worked but the
   captures prove what the proxy served; say so. `EGRESS FAILED OPEN` means the
   declared vantage point did not hold — lead with that.
5. If neither command works, say so plainly and stop. Do not fall back to
   ad-hoc web lookups presented as a result: without the tool there are no
   evidence bands, no correlation groups, no audit record and no evidence
   package, and an answer that looks the same without them is the failure this
   skill exists to prevent. Say which part is missing and offer the installed
   route (`pipx install attribution-suite`) for a verifiable run.

## Reporting

- If the summary says `SELECTIVE SERVING`, lead with **that**: the site
  declared different accounts to different retrievals, so the payee below is
  the copy this tool was shown, not the site's public declaration.
- Otherwise lead with the `PAYEE` block: who the ad system says it pays.
- `DIRECT` names the seller. `RESELLER` names an intermediary and is **not**
  the party being paid.
- Give the band and the number of independent evidence groups. Bands are
  evidence strength, **not** probability — never restate one as a percentage.
- Exit code 3 is INCOMPLETE, not failure: say which budget or block stopped the
  search, and that an absence may mean "could not be checked".
- Any conclusion about a natural person is a **lead** until a statutory
  register corroborates it. Say that when you report one.
- In offline mode, keep the **OFFLINE REPLAY** banner when you relay the
  result: it states that the retrievals were not the toolkit's own and that
  the digests commit to what was saved rather than to what the origin served.
- `--stdout` puts the whole report in front of you: summarise it in the
  conversation. Say that the evidence package was also written, and where, but
  never answer by pointing at a path the reader cannot open.
- Lead from the report's own **Executive summary** rather than rewriting it,
  and carry any condition it names into your first sentence.
- Pass the **Network map** `mermaid` fence through unchanged if this surface
  renders mermaid; otherwise describe the shape in one sentence. Do not redraw
  it. Arrow labels are bands, not probabilities.
- **Indicators** rows marked `DEMOTED` stay in the table on purpose — a
  planted identifier is a fact about whoever planted it. Never pass one on as
  a clean indicator.
- A **gravatar** link means the page displays that address's avatar, computed
  from values already collected. It does **not** mean the operator controls
  the address. Report it with that caveat attached.

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
   `$ARGUMENTS` already says. Pass it verbatim. Do not invent one. A run
   without it records itself as unattested in the audit trail.
3. Run it:

   ```bash
   attribution-suite --domain <domain> --authorization "<what the user said>"
   ```

   Add `--show-person` only if the user asked for it explicitly.
4. If the command is not found, run `attribution doctor` and report what it
   says rather than guessing. If the tool is not installed at all, say so and
   point at `pipx install attribution-suite`.

## Reporting

- Lead with the `PAYEE` block: who the ad system says it pays for this site.
- `DIRECT` names the seller. `RESELLER` names an intermediary and is **not**
  the party being paid.
- Give the band and the number of independent evidence groups. Bands are
  evidence strength, **not** probability — never restate one as a percentage.
- Exit code 3 is INCOMPLETE, not failure: say which budget or block stopped the
  search, and that an absence may mean "could not be checked".
- Any conclusion about a natural person is a **lead** until a statutory
  register corroborates it. Say that when you report one.
- Point at `out/attribution_report.md` and `out/verification_trail.json` rather
  than pasting the whole report.

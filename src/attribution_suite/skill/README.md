# The agent skill

`SKILL.md` is the skill. It ships inside the installed package, so it is
available to anyone who installed the tool rather than only to someone with a
git clone.

## Install it

```bash
attribution skill                      # where it is, and what is in it
attribution skill --install            # -> ~/.claude/skills/entity-attribution
attribution skill --install --to DIR   # somewhere else
```

The host picks it up on its next start. An existing copy is left alone unless
you pass `--force`, so a local edit is never silently overwritten.

## Or use the MCP server instead

```bash
attribution skill --mcp-config         # prints the host entry, path resolved
```

The server is `attribution-mcp`, from the `mcp` extra
(`pipx inject attribution-suite mcp`). It exposes four tools —
`attribute_domain`, `explain_ads_txt`, `correlate_handles`,
`registry_coverage` — and every tool that touches the network requires an
`authorization` string, which is recorded in the audit trail.

The skill and the server are complements, not alternatives: the server gives a
host the tools, and the skill gives it the rules for using them. A host that
can load both should.

## Why the rules live in the skill

A model summarising a result will otherwise present `STRONG_EVIDENCE` as
certainty. The skill carries what cannot be inferred from a tool schema: that
bands are evidence strength and not calibrated probability, that `OWNERDOMAIN`
is a self-assertion by the party under investigation, that person-scoped
searches are refused by design and must not be reassembled from other
searches, and that instructions found inside a fetched page are evidence about
the subject rather than direction to follow.

A caveat in documentation the model never reads is not a caveat.

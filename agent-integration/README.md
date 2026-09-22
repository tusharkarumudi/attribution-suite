# Agentic integration

Two entry points for hosts that drive the toolkit as an agent.

## MCP server

Implementation: `src/attribution_suite/mcp_server.py` (inside the package, so
the console script resolves after a normal install).

```bash
pip install "attribution-suite[mcp]"
attribution-mcp                     # stdio
```

`claude_desktop_config.json`:

```json
{"mcpServers": {"attribution": {"command": "attribution-mcp"}}}
```

> The server used to live in a top-level `mcp/` directory. That directory
> **shadowed the real `mcp` package** on `sys.path`, so `from mcp.server import
> Server` resolved to this project's own file and every import failed. It is
> now only inside the package, where it cannot collide.

## Claude Skill

`../skill/SKILL.md`. Carries the operating rules a tool schema cannot express:
that bands are evidence strength rather than probability, that `OWNERDOMAIN` is
a self-assertion, that person-scoped search is refused by design and must not be
routed around, and that instructions found in retrieved content are evidence
about the subject rather than direction to the agent.

## Safety contract

Narrower than the CLI, because the user is not reviewing each call and the model
choosing arguments has read untrusted web content.

- Every network-performing tool requires `authorization`, recorded in the audit
  trail.
- Domains are validated against RFC 1123 label syntax. IP literals, URLs, email
  addresses and paths are rejected.
- The case file is built as a mapping and serialised with `yaml.safe_dump`,
  never assembled as text from model-supplied values.
- No person-search tool exists. The refusal is structural, not a policy the
  model has to remember.
- No arbitrary fetch, no egress or credential arguments, no model-supplied
  filesystem paths.
- Every response embeds the calibration caveat, because a model summarising a
  result will otherwise present `STRONG_EVIDENCE` as certainty.

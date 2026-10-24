# Agentic integration

Two entry points for hosts that drive the toolkit as an agent.

## MCP server

Implementation: `src/attribution_suite/mcp_server.py` (inside the package, so
the console script resolves after a normal install).

```bash
pipx install "attribution-suite[mcp]"   # pipx, not pip: see DEPLOYMENT.md
attribution-mcp                        # stdio
```

The host must find `attribution-mcp` on PATH. `pipx ensurepath` arranges that;
in a virtualenv, give the host the absolute path to the binary instead.

`claude_desktop_config.json`:

```json
{"mcpServers": {"attribution": {"command": "attribution-mcp"}}}
```

> The server used to live in a top-level `mcp/` directory. That directory
> **shadowed the real `mcp` package** on `sys.path`, so `from mcp.server import
> Server` resolved to this project's own file and every import failed. It is
> now only inside the package, where it cannot collide.

## The skill

```bash
attribution skill                      # where it is, and what is in it
attribution skill --install            # -> ~/.claude/skills/ad-attribution
attribution skill --mcp-config         # the host entry for MCP, path resolved
```

It ships inside the package (`attribution_suite/skill/SKILL.md`). It used to
live in a top-level `skill/` directory, which meant it existed only in a git
checkout: every person who followed the quickstart and installed from PyPI got
a tool whose agent-facing half was missing, with nothing saying so.
`attribution doctor` now reports whether it is installed for a host.

It carries the operating rules a tool schema cannot express:
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

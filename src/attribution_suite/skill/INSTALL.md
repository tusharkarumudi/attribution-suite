# Installing this bundle

One bundle, three ways in. Which one you use depends on whether your assistant
can **run a command** — because the investigation is a program that fetches
`ads.txt`, `sellers.json` and public registers. A chat window that cannot run
anything can read these rules, but it cannot do the work.

| Where | What to do | Can it actually investigate? |
|---|---|---|
| Claude (web or desktop) | upload `ad-attribution.zip` under **Customize → Skills** | only via the MCP server below |
| Claude Code | `attribution skill --install`, or unzip into `~/.claude/skills/` | **yes** — it has a shell |
| Gemini CLI / Antigravity, Cursor, OpenCode | copy the `ad-attribution/` folder into that tool's skills directory | **yes**, if the CLI is installed |
| Any MCP host | the config below | **yes** — the server does the work |
| A plain chat with no tools | attach `SKILL.md` as a file | no: it can only explain the method |

## 1. The skill

The zip's top level is the `ad-attribution/` directory, which is what Claude
expects. Upload it under **Customize → Skills** and enable it.

For a filesystem-based host, put the folder where that host looks:

```bash
~/.claude/skills/ad-attribution/          # Claude Code (or .claude/skills/ in a project)
~/.agents/skills/ad-attribution/          # Gemini CLI / Antigravity and other
                                          # readers of the same layout
```

`attribution skill --install` does the Claude Code case for you, and
`attribution skill --install --to DIR` any other.

Once it is in place, ask in plain language — *"who operates example.com?"*,
*"what does this site's ads.txt reveal?"* — and the host loads the skill from
its description.

## 2. The slash command (Claude Code)

A skill is loaded when the model judges it relevant. For an explicit command,
copy the file in `commands/`:

```bash
mkdir -p ~/.claude/commands
cp commands/ad-attribution.md ~/.claude/commands/
```

Then:

```
/ad-attribution investigate example.com
```

Project-scoped instead of user-scoped: `.claude/commands/` inside the repo.

## 3. The MCP server — the part that does the work

This is what gives an assistant the actual capability, and it is the only
option that works in a chat window with no shell. It needs Python on the
machine running the host.

```json
{
  "mcpServers": {
    "attribution": {
      "command": "uvx",
      "args": ["--from", "attribution-suite[mcp]", "attribution-mcp"]
    }
  }
}
```

`uvx` downloads and runs it on first use, so there is nothing to install
beforehand. If you would rather install it yourself:

```bash
pipx install "attribution-suite[mcp]"
attribution skill --mcp-config          # prints the same block, path resolved
```

Where the config goes depends on the host — Claude Desktop keeps it in
`claude_desktop_config.json`, and editors that speak MCP have their own
`mcp.json`. `mcp.json` in this bundle is the same block, ready to paste.

The server exposes four tools: `attribute_domain`, `explain_ads_txt`,
`correlate_handles` and `registry_coverage`. **Every tool that touches the
network requires an `authorization` string**, which is recorded in the audit
trail. There is deliberately no tool that searches for a person by name.

## Check it worked

```bash
attribution doctor
```

The `AGENT SURFACE` section reports whether the skill is installed for a host
and whether the MCP server is reachable, with the command to fix either.

## What this bundle is not

It does not carry the toolkit — `attribution-suite` is a Python package from
PyPI, and the skill tells an assistant how to drive it. Attaching this to a
chat that has no way to run commands gives that chat the method and the
reporting rules, not the capability. Anyone who claims otherwise is selling a
prompt as a program.

# Installing this bundle

**The short version: upload the zip and you are done.** `attribution_portable.py`
travels inside it, needs nothing installed and no network of its own, and runs
anywhere there is Python. Everything below is about getting *more* than that.

This used to open by saying the toolkit was a separate package from PyPI and
that a chat window could only explain the method. Both were true and both were
the problem: chat sandboxes refuse `pip install` as external code, so the skill
reported "command not found" and the assistant fell back to ad-hoc web lookups
— losing the evidence bands, correlation groups, audit record and evidence
package, which is everything the toolkit is for.

| Where | What to do | What it can do |
|---|---|---|
| Claude, ChatGPT or Gemini on the web | upload `ad-attribution.zip` (Claude: **Customize → Skills**); elsewhere attach the folder's files | **the full analysis.** The assistant fetches with its own browsing tool and `attribution_portable.py` does the rest |
| Claude Code, Gemini CLI, Cursor, OpenCode | copy `ad-attribution/` into that tool's skills directory | **everything**, including the installed CLI if it is present |
| Any MCP host | the config below | everything, with the server doing the retrieval |
| A chat with no code execution at all | attach `SKILL.md` | explain the method only — nothing can run |

## 0. Offline mode — what makes the first row work

`attribution_portable.py` is the whole analysis chain in one file: the real
modules, assembled by `scripts/build_portable.py`, standard library only,
Python 3.11+. It is not a cut-down reimplementation — a test asserts it
produces the same report as the installed packages from the same documents,
because a second copy of the scoring rules would drift and the dead copy is
the one that gets corrected.

It makes **no network requests**. It does not need to: the assistant can
already reach the web, and its sandbox cannot. So the work splits along that
line.

```bash
python attribution_portable.py example.com -a "ticket: ACME-1234"
```

With no documents yet it prints the URLs to fetch and the filename for each.
Fetch them, save the bodies verbatim into `./fetched/`, run it again. Two
rounds is normal: `ads.txt` first, then the `sellers.json` files it names.

What it gives you that ad-hoc lookups do not: evidence bands with their
correlation groups, the adversarial checks, gravatar binding, the executive
summary, the network map, the indicator table, an append-only audit trail
carrying your stated authorisation, and a SHA-256 for every document read.

What it cannot give you: evidence the *toolkit* retrieved. The digests commit
to what was saved, not to what the origin served, and the report says so in a
banner. For a result someone else must verify independently, use the installed
toolkit.

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

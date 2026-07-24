# paper-claude-plugins

A personal Claude Code plugin marketplace.

## What's inside

**contracts** — bundles:
- `generate-msa` skill (draft a Master Service Agreement from a prompt or template)

**metrics** — bundles:
- `sales-cycle` skill (average/median sales cycle length analysis)
- `win-rate` skill (count- and dollar-based win rate analysis)
- Salesforce Custom MCP connector (read-only: SOQL, SOSL, schema tools)

## First-time setup (on any machine)

1. Push this folder to a git repo (e.g. a private GitHub repo).
2. In Claude Code, add the marketplace:
   ```
   /plugin marketplace add <your-repo-url-or-path>
   ```
3. Install the plugins:
   ```
   /plugin install contracts@paper-claude-plugins
   /plugin install metrics@paper-claude-plugins
   ```
4. Run `/mcp` inside a session to authenticate the Salesforce connector if it's not already connected.
5. Run `/reload-plugins` if you make edits later without restarting.

## Updating

Edit the skill files directly under `plugins/contracts/skills/` and `plugins/metrics/skills/`, commit, and push.
Anyone (including future-you on another machine) picks up changes via:
```
/plugin marketplace update paper-claude-plugins
```

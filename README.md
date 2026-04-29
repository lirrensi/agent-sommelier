# AgentCLI Helpers

> Desktop superpowers for your AI agent. Simple CLI tools that just work.

## Skills

This repo includes **agent skills** for self-installing tools. Install via npm:

```bash
npx skills add https://github.com/lirrensi/agent-cli-helpers
```

Available skills:

| Skill | Description |
|-------|-------------|
| `bg-jobs` | Background jobs that don't disappear |
| `crony` | Cron jobs with natural language scheduling |
| `desktop-notifications` | Cross-platform desktop notifications |
| `document-extractor` | Convert PDFs, Office docs, media, and other inputs to Markdown |
| `screenshot` | Screen capture that actually works |
| `tmux` | Terminal multiplexer for SSH, REPLs, and parallel agents |
| `edge-tts` | Text-to-speech using Microsoft's edge-tts |
| `memory-bank` | Lightweight persistent memory across conversations |
| `batch-task-executor` | Experimental input-agnostic batch orchestration for many similar tasks |

## The Problem

You've got an AI agent with bash access. That's powerful — but the raw APIs are *rough*.

Want to run something in the background? Hope you remember `nohup`, because the output is going to `nohup.out` somewhere and tracking jobs is on you.

Need a notification? Good luck cross-platform. Windows, macOS, Linux all do it differently.

Cron jobs? Great, but writing cron expressions at 3am? Not exactly human-friendly.

And don't get me started on screenshots.

## The Solution

A small collection of CLI tools that wrap the messy stuff. No daemon. No database. Just files and commands that behave the way you expect.

```bash
# Install from GitHub
uv tool install "git+https://github.com/lirrensi/agent-cli-helpers"
```

That's it. Pick what you need:

| Tool | What it does | Install |
|------|--------------|---------|
| `crony` | Cron jobs with natural language scheduling | `uv tool install "git+https://github.com/lirrensi/agent-cli-helpers#crony"` |
| `notify` | Cross-platform desktop notifications | Built-in |
| `bg` | Background jobs that don't disappear | Built-in |
| `screenshot` | Screen capture that actually works | `uv tool install "git+https://github.com/lirrensi/agent-cli-helpers#screenshot"` |

Or install everything:
```bash
uv tool install "git+https://github.com/lirrensi/agent-cli-helpers"
```

## Tools

### crony — Cron jobs, human-readable

```bash
# Instead of "*/15 * * * *", just say what you mean:
crony add health-check "every 1h" "curl -s http://localhost:8080/health"
crony add backup "every day at 2am" "backup.sh"
crony add reminder "in 30m" "notify 'Meeting' 'Starting soon!'"

# Manage them
crony list
crony run health-check
crony rm backup
```

Supports natural language: `every 1h`, `every monday`, `at 15:30`, `in 5m`, `on 2026-03-15`.

### notify — Desktop notifications

```bash
# Simple
notify "Build Done" "All tests passed!"

# Pipe-friendly
curl -s http://api/status | notify "API Check"

# Chain it
long-task && notify "Complete" "Finished successfully"
```

Works on Windows, macOS, and Linux. No platform-specific code in your scripts.

### bg — Background jobs, tracked

`bg` runs detached commands without tmux or a second terminal. `bg run` returns immediately after creating the job handle; a detached worker finishes the launch in the background and jobs appear running unless failure is proven. A short best-effort PID probe updates the record after a few seconds when it can. On Windows it prefers PowerShell 7, then Windows PowerShell, then `cmd.exe`, and launches jobs without a visible console window when PowerShell is available. Terminal jobs are auto-pruned after 1 hour and capped at 32 records, while running jobs are never evicted. It also supports `bg wait`, `bg wait --match`, `bg wait-all`, and `bg prune`.

```bash
# Bash / zsh
JOB_NAME=$(bg run "python train_model.py")
bg status "$JOB_NAME"
bg logs "$JOB_NAME"
bg wait "$JOB_NAME"
bg wait "$JOB_NAME" --match "ready"
bg wait-all
```

```powershell
# PowerShell
$jobName = bg run "python train_model.py"
bg status $jobName
bg logs $jobName
bg wait $jobName
bg wait $jobName --match "ready"
bg wait-all
```

Runtime state lives under your OS temp directory in `agentcli_bgjobs/` with `index.json` and per-job `records/<uid>/meta.json`, `stdout.txt`, `stderr.txt`, and `exit_code.txt`.

### screenshot — Screen capture

```bash
# Auto-named
screenshot
# → ~/Temp/agentcli_screenshots/screenshot_20260305_160405.png

# Name it yourself
screenshot bug_report.png

# Use in scripts
path=$(screenshot)
notify "Captured" "$path"
```

Cross-platform using `mss` library, with fallbacks to native tools on Linux.

## For AI Agents

This repo also includes **skills** — instructions your agent can use to self-install tools on demand.

```
skills/
├── bg-jobs/SKILL.md
├── crony/SKILL.md
├── desktop-notifications/SKILL.md
├── document-extractor/SKILL.md
├── edge-tts/SKILL.md
├── memory-bank/SKILL.md
├── batch-task-executor/SKILL.md
├── screenshot/SKILL.md
└── tmux/SKILL.md
```

The pattern is simple:
1. Agent checks if tool exists: `crony --help`
2. If not, install it: `npx skills add https://github.com/lirrensi/agent-cli-helpers`
3. Use it

No MCP servers. No configuration. No OAuth. Just tools.

## Why This Exists

You can do all of this in raw bash. Seriously — background jobs, notifications, cron, screenshots — it's all possible with the right incantations.

But it's *ugly*. It's *error-prone*. And writing 3 lines of PowerShell just to show a notification is a waste of energy.

This repo exists because AI agents deserve desktop superpowers without the friction. The command line is already there — we just need it to be *nicer*.

- **One-liner APIs** instead of 10 lines of boilerplate
- **Consistent behavior** across Windows, macOS, and Linux
- **Error handling** that doesn't make you cry at 3am
- **No daemon to manage** — just files, folders, and commands that work

## Requirements

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

## License

MIT — do whatever, just don't blame me if your cron jobs delete your prod database.

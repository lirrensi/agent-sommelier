
<p align="center">
  <br>
  <img src="https://img.shields.io/badge/agent--sommelier-0.8.5-8B5CF6?style=flat-square" alt="version">
  <img src="https://img.shields.io/badge/python-%3E%3D3.10-2D9CDB?style=flat-square" alt="python">
  <img src="https://img.shields.io/badge/license-MIT-27AE60?style=flat-square" alt="license">
  <br><br>
</p>

<h1 align="center">🍷 Agent Sommelier</h1>

<p align="center">
  <strong>A capability sommelier for your coding agent.</strong><br>
  Tools, skills, and systems — curated on demand, workflow agnostic.
</p>

<p align="center">
  <code>uv tool install "git+https://github.com/lirrensi/agent-sommelier"</code>
</p>

<br>

---

## Why Agent Sommelier?

You have a bare-bones app. You need to add functionality — background jobs, notifications, task tracking, memory, cron scheduling. You *could* build each one from scratch. Or you could reach for a tool that already does it, does it well, and doesn't lock you into a workflow.

That's this repo.

**Agent Sommelier is not a workflow system.** It doesn't make plans for you, orchestrate pipelines, or prescribe how you should work. What it *does* is give you **individual, independent tools** — each one a self-contained capability you can slot into anything.

- **Already have a cron setup but hate how it works?** Here's a better one.
- **Need background jobs but don't want to wrestle with `nohup`?** Grab ours.
- **Want your agent to remember things between conversations?** Memory's right here.
- **Need a task system that doesn't require a database?** Done.

Pick the tools you need. Leave the rest. They all work independently — and they all work together when you want them to.

---

## At a Glance

### CLI Tools — Desktop superpowers for your agent

| Tool | What | Install |
|------|------|---------|
| `crony` | Cron jobs in plain English | `uv tool install "git+https://github.com/lirrensi/agent-sommelier"` |
| `notify` | Cross-platform desktop notifications | Built-in |
| `bg` | Background jobs, tracked by name | Built-in |
| `screenshot` | Screen capture, zero fuss | `uv tool install "git+https://github.com/lirrensi/agent-sommelier"` |
| `tasks` | In-repo task management with deps & queues | Built-in |
| `skill-store` | On-demand skill registry — load only what you need | Built-in |

### Skills — Agent instructions for self-installing tools

```bash
npx skills add https://github.com/lirrensi/agent-sommelier
```

| Skill | What it gives your agent |
|-------|--------------------------|
| `bg-jobs` | Run & track background processes without tmux |
| `crony` | Schedule jobs with "every 1h" instead of cron expressions |
| `desktop-notifications` | One command, three OSes |
| `screenshot` | Capture screens in scripts and pipelines |
| `document-extractor` | Convert PDFs, Office docs, media to Markdown |
| `edge-tts` | Text-to-speech via Microsoft Edge |
| `tmux` | Terminal multiplexing for SSH, REPLs, parallel agents |
| `memory-bank` | **Core.** Episodic, semantic, procedural memory across sessions |
| `task-system` | Full task lifecycle — deps, queues, 12 statuses, permanent history |
| `skill-store` | Lazy-load hundreds of skills without polluting context |
| `batch-task-executor` | Fan-out orchestration from any task source |
| `best-practices-researcher` | Research patterns and make technology decisions |
| `micropatch` | Keep fork features alive across upstream updates |
| `calm-down` | De-escalation when the agent is heading the wrong way |

---

## The Tools

### ⏰ crony — Cron jobs, human-readable

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

Supports: `every 1h` · `every monday` · `at 15:30` · `in 5m` · `on 2026-03-15`

Works standalone or as a smarter cron replacement in any setup.

---

### 🔔 notify — Desktop notifications

```bash
# Simple
notify "Build Done" "All tests passed!"

# Pipe anything
curl -s http://api/status | notify "API Check"

# Chain after long tasks
long-task && notify "Complete" "Finished successfully"
```

Windows · macOS · Linux. One command, all three.

---

### ⚙️ bg — Background jobs, tracked

Run commands detached — no tmux, no second terminal, no `nohup` madness.

```bash
# Bash / zsh
JOB_NAME=$(bg run "python train_model.py")
bg status "$JOB_NAME"
bg logs "$JOB_NAME"
bg wait "$JOB_NAME" --match "ready"
bg wait-all
bg prune   # Clean up finished jobs
```

```powershell
# PowerShell
$jobName = bg run "python train_model.py"
bg status $jobName
bg logs $jobName
bg wait $jobName
bg wait-all
```

- Auto-prunes terminal jobs after 1 hour (running jobs are never evicted)
- Handles Windows, macOS, Linux shell differences transparently
- Cap at 32 records — no unbounded log bloat

---

### 📸 screenshot — Screen capture

```bash
# Auto-named with timestamp
screenshot
# → ~/Temp/agentcli_screenshots/screenshot_20260305_160405.png

# Name it yourself
screenshot bug_report.png

# Use in scripts
path=$(screenshot)
notify "Captured" "$path"
```

Cross-platform via `mss`, with native fallbacks on Linux.

---

### 📋 tasks — Task management, in-repo

A task system that lives in your repo. No database. No service. Just YAML files and a CLI.

```bash
# Bootstrap
tasks init

# Create
tasks add "Refactor the auth module"
tasks add "Fix login bug" --tag bug --tag auth --priority p0

# See what's next
tasks next                          # Top priority
tasks ready                         # All unblocked, sorted
tasks blocked                       # What's stuck and why
tasks status                        # Overview: in-progress, blockers, etc.
tasks overview                      # Read-only, visually separated active-task overview

# Work through
tasks update TSK-0042 --status in-progress --notes "Found root cause"
tasks update TSK-0042 --evidence "file: src/agent_sommelier/tasks/core.py"
tasks close TSK-0042 --note "Deployed to staging" --evidence "docs/release-notes.md"

# Search
tasks search login                  # Full-text across all tasks
tasks list --tag security           # Filter active by tag
tasks history --limit 10            # Recently completed
```

**12 statuses** — `todo` → `in-progress` → `done` (with `blocked`, `postponed`, `cancelled`, `review`, `waiting`, `parked`, `deferred`, `backlog`, `abandoned` in between). Move freely — no transition restrictions.

**Dependencies with teeth** — `blocks`, `parent`, `child`, `discovered`, `relates`. The `blocks` type feeds the ready queue automatically.

**Read-only overview when you need a scan pass** — `tasks overview` gives you a vertically separated, Rich-formatted summary of active work without changing task state.

**Tagging that scales** — Multi-dimensional: `--tag type:bug`, `--tag area:auth`, `--tag urgency:security`.

---

### 🧠 skill-store — Skill registry, on demand

A local registry for agent skills. Keep hundreds on disk, load only what you need into context.

```bash
# Set it up once
skill-store init

# Browse
skill-store list                    # Paginated, pinned first
skill-store search web              # Full-text search

# Load when needed
skill-store load crony              # Show path + folder tree
skill-store preview crony           # Read first 100 lines

# Organize
skill-store pin crony               # Move to top
skill-store create-new              # Interactive scaffolding wizard
skill-store sync                    # Rebuild index, git commit
```

---

## For AI Agents

This repo is designed *for* agents. Each tool can be self-installed on demand using the included skills:

```
skills/
├── bg-jobs/SKILL.md
├── crony/SKILL.md
├── desktop-notifications/SKILL.md
├── document-extractor/SKILL.md
├── edge-tts/SKILL.md
├── memory-bank/SKILL.md            # ← Core: persistent context
├── task-system/SKILL.md
├── skill-store/SKILL.md            # ← Lazy-load hundreds of skills
├── batch-task-executor/SKILL.md
├── screenshot/SKILL.md
├── tmux/SKILL.md
├── best-practices-researcher/SKILL.md
├── calm-down/SKILL.md
├── micropatch/SKILL.md
```

The pattern:

1. Agent checks if tool exists: `crony --help`
2. If not, installs it: `npx skills add https://github.com/lirrensi/agent-sommelier`
3. Uses it

No MCP servers. No OAuth. No config files. Just tools that agents can reach for.

> **Memory-bank** is the core skill — it keeps your agent's context alive across sessions.
> Three memory types (episodic, semantic, procedural), auto-generated INDEX.md with tag search,
> Obsidian-compatible templates. Run `bat ./memory/INDEX.md` to orient yourself when resuming work.

---

## Repository Map

```
agent-sommelier/
├── src/agent_sommelier/      # CLI tool implementations (Python)
│   ├── __init__.py
│   ├── notify.py             # Desktop notifications
│   ├── bg.py                 # Background job manager
│   ├── crony.py              # Cron job scheduler
│   ├── screenshot.py         # Screen capture
│   ├── tasks/                # In-repo task management package
│   │   ├── __init__.py
│   │   ├── cli.py
│   │   ├── core.py
│   │   └── render.py
│   └── skill_store.py        # On-demand skill registry CLI
├── skills/                   # Agent skill definitions (14 skills)
├── docs/                     # Product & architecture documentation
├── agent_chat/               # Design discussions & execution plans
├── tests/                    # Test suite (350+ tests)
├── pyproject.toml            # Package metadata & entry points
├── uv.lock                   # Locked dependencies
└── README.md
```

---

## Requirements

- **Python 3.10+**
- **[uv](https://github.com/astral-sh/uv)** (recommended) or pip

---

## Philosophy

**Agent Sommelier is not a framework. It's a toolbox.**

We don't care *how* you work. We don't prescribe workflows, make plans for you, or assume you use any particular stack. What we *do* is give you standalone, well-crafted capabilities that you can:

- **Slot into an existing setup** — already use cron? Replace it with `crony` one-for-one.
- **Use alongside anything** — every tool is independent and composable.
- **Ignore the rest** — don't need background jobs? Don't install them. Zero overhead.

If you're building a plan, an orchestrator, or a workflow engine — that's *your* job. Ours is making sure you have the right instruments in hand when you do.

---

## License

MIT — do whatever, just don't blame us if your cron jobs delete your prod database.

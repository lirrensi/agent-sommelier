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
| `memory-bank` | **Core.** Persistent memory across conversations — episodic, semantic, procedural. Tags, templates, auto-indexed. For any domain: code, business, personal, research. |
| `batch-task-executor` | Experimental input-agnostic batch orchestration for many similar tasks |
| `best-practices-researcher` | Meta-skill for researching best practices and making technology decisions |
| `calm-down` | De-escalation protocol when the agent is heading the wrong direction |
| `micropatch` | Semantic fork customization — keep fork features alive across upstream updates |
| `task-system` | In-repo task management with dependencies, ready/blocked queues, search, 12 statuses, tagging strategy, permanent history |
| `skill-store` | On-demand skill loading from a local registry. Lazy-load hundreds of skills without polluting context. |

## The Problem

You've got an AI agent with bash access. That's powerful — but the raw APIs are *rough*.

Want to run something in the background? Hope you remember `nohup`, because the output is going to `nohup.out` somewhere and tracking jobs is on you.

Need a notification? Good luck cross-platform. Windows, macOS, Linux all do it differently.

Cron jobs? Great, but writing cron expressions at 3am? Not exactly human-friendly.

And don't get me started on screenshots.

Then there's **task management**. You start a session, you plan some work, the agent does its thing — but who's tracking what's left? Without a task system, context evaporates between sessions.

And **memory** itself — every conversation starts from scratch. The agent can't remember last session's discoveries, the architecture decisions you made, the client preferences you uncovered, or the workflow you debugged together for an hour. That's not just friction; it's lost knowledge.

## The Solution

A small collection of CLI tools that wrap the messy stuff. No daemon. No database. Just files and commands that behave the way you expect.

```bash
# Install from GitHub
uv tool install "git+https://github.com/lirrensi/agent-cli-helpers"
```

That's it. Pick what you need — including the **memory-bank** skill for persistent context and **skill-store** for lazy-loading hundreds more:

| Tool | What it does | Install |
|------|--------------|---------|
| `crony` | Cron jobs with natural language scheduling | `uv tool install "git+https://github.com/lirrensi/agent-cli-helpers#crony"` |
| `notify` | Cross-platform desktop notifications | Built-in |
| `bg` | Background jobs that don't disappear | Built-in |
| `screenshot` | Screen capture that actually works | `uv tool install "git+https://github.com/lirrensi/agent-cli-helpers#screenshot"` |
| `tasks` | In-repo task management with deps, queues, search, 12 statuses | Built-in |
| `skill-store` | On-demand skill registry — list, search, load, pin, create | Built-in |

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

`bg` runs detached commands without tmux or a second terminal. `bg run` returns immediately after creating the job handle; a detached worker finishes the launch in the background and jobs appear running unless failure is proven. A short best-effort PID probe updates the record after a few seconds when it can. On Windows it prefers PowerShell 7, then Windows PowerShell, then `cmd.exe`, and launches jobs without a visible console window when PowerShell is available. Terminal jobs are auto-pruned after 1 hour and capped at 32 records, while running jobs are never evicted. It also supports `bg wait`, `bg wait --match`, `bg wait-all`, `bg prune`, and `bg restart`.

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

### tasks — Task management, in-repo

A lightweight, file-based task system that lives in the repo. No database, no service, no setup beyond `tasks init`. Tasks are permanent history — once created, they're never deleted.

```bash
# Bootstrap (creates tasks/ directory)
tasks init

# Create a task
tasks add "Refactor the auth module"
tasks add "Fix login bug" --tag bug --tag auth --priority p0
tasks add "Write tests" --dep TSK-0003:blocks --notes "Must cover edge cases"

# See what needs doing
tasks list                         # All active tasks
tasks list --status todo --tag bug # Filter by status + tags
tasks list --tag-any urgent --tag-any security  # OR filter
tasks list --text "login"          # Full-text search
tasks list --json                  # Machine-readable

# Smart queues
tasks next                         # Highest-priority todo
tasks next --take 3                # Top 3
tasks next --skip-blocks           # Exclude blocked tasks
tasks ready                        # Top 5 unblocked tasks
tasks blocked                      # What's stuck and why

# Session overview
tasks status                       # In-progress, blockers, priorities, tags, inbox count
tasks status --json                # Machine-readable

# Get details
tasks show TSK-0042                # Full detail with deps resolved inline

# Update progress
tasks update TSK-0042 --status in-progress --priority high
tasks update TSK-0042 --notes "Investigation found race condition"
tasks update TSK-0042 --dep TSK-0005:blocks      # Blocked by another task

# Close when done
tasks close TSK-0042
tasks close TSK-0042 --note "Fixed, deploying to staging"

# Browse archive
tasks history                      # Recently closed (default: 30)
tasks history --limit all          # Full archive
tasks history --offset 30          # Paginate
tasks history --tag deploy         # Filter closed by tag
tasks history --text "auth"        # Search closed archive

# Search everything
tasks search login                 # Full-text across all tasks
tasks search "dark mode" --in title  # Field-scoped search

# Dump raw ideas
tasks inbox
```

**12 statuses** — `todo`, `in-progress`, `done`, `blocked`, `postponed`, `cancelled`, `review`, `waiting`, `parked`, `deferred`, `backlog`, `abandoned`. Move freely between them — no restrictions on transitions.

**Typed dependencies** — Tasks can declare `blocks`, `parent`, `child`, `discovered`, and `relates` relationships. The `blocks` type feeds the ready queue: `tasks ready` shows only unblocked work, `tasks blocked` shows what's stuck and why.

**Multi-dimensional tagging** — Tag tasks by type (`bug`, `feature`, `docs`), area (`auth`, `ui`, `api`), qualifier (`security`, `urgent`, `breaking-change`), process (`needs-review`, `autonomous-ready`), or anything project-specific. Combine freely with `--tag` (AND) and `--tag-any` (OR) filters.

**Notes as a coordination log** — Every `--notes` update appends to an array, giving each task a running history of decisions, blockers, and progress.

Data lives in `tasks/tasks.yaml` (active) and `tasks/closed.yaml` (archive). The `tasks/` directory is gitignored — each clone has its own local task state. Use `tasks inbox` for free-form idea dumps in `tasks/inbox.md`.

### skill-store — Skill registry, on demand

A CLI for managing a local agent skill registry at `~/.skill-store/`. List, search, load, pin, create, and sync skills with automatic git versioning — without polluting your agent's context window.

```bash
# Initialize the store
skill-store init

# Browse what's available
skill-store list                    # Paginated, pinned first
skill-store search web              # Full-text search names + descriptions
skill-store search auth --json      # Machine-readable output

# Load a skill when you need it
skill-store load crony              # Show path + folder tree
skill-store preview crony           # Read first 100 lines of SKILL.md

# Organize
skill-store pin crony               # Move to top of list
skill-store unpin crony

# Create your own
skill-store create-new              # Interactive scaffolding wizard

# Keep it in sync
skill-store sync                    # Scan skills, rebuild index, git commit
```

Skills are stored as flat files in `~/.skill-store/skills/<slug>/SKILL.md` with a git-backed index. Use it as a lazy loader: keep hundreds of skills on disk, load only what you need into context.

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
├── tmux/SKILL.md
├── best-practices-researcher/SKILL.md
├── calm-down/SKILL.md
├── micropatch/SKILL.md
├── task-system/SKILL.md
└── skill-store/SKILL.md       # ← New: on-demand skill loading from local registry
```

The pattern is simple:
1. Agent checks if tool exists: `crony --help`
2. If not, install it: `npx skills add https://github.com/lirrensi/agent-cli-helpers`
3. Use it

No MCP servers. No configuration. No OAuth. Just tools.

> **Memory-bank is a core skill** — it keeps your agent's context alive across sessions.
> Three memory types (episodic, semantic, procedural), auto-generated INDEX.md with tag search,
> Obsidian-compatible templates, and a bias toward usefulness across any domain — code, business,
> personal, research, creative, or client work. Run `bat ./memory/INDEX.md` to orient yourself
> whenever resuming a project.

## Repository Map

```
AgentCLI_Helpers/
├── src/agentcli_helpers/    # CLI tool implementations (Python)
│   ├── __init__.py
│   ├── notify.py            # Desktop notifications
│   ├── bg.py                # Background job manager
│   ├── crony.py             # Cron job scheduler
│   ├── screenshot.py        # Screen capture
│   ├── tasks.py             # In-repo task management
│   └── skill_store.py       # On-demand skill registry CLI
├── skills/                  # Agent skill definitions (14 skills)
│   ├── memory-bank/         # Core. Episodic/semantic/procedural memories
│   ├── task-system/         # In-repo task management with deps & queues
│   ├── skill-store/         # On-demand skill loading from local registry
│   ├── bg-jobs/             # Background jobs
│   ├── crony/               # Natural language cron scheduling
│   └── ...                  # (9 more skills)
├── docs/                    # Architecture & product documentation
│   ├── product.md           # Behavior specs & CLI reference
│   └── arch.md              # Implementation details
├── agent_chat/              # Design discussions & execution plans
├── tests/                   # Test suite
├── private/                 # Private scratch notes (gitignored)
├── tasks/                   # Local task files (gitignored)
├── pyproject.toml           # Package metadata & entry points
├── uv.lock                  # Locked dependencies
└── README.md
```

## Why This Exists

You can do all of this in raw bash. Seriously — background jobs, notifications, cron, screenshots, task tracking, persistent memory, skill registries — it's all possible with enough shell incantations.

But it's *ugly*. It's *error-prone*. And writing 3 lines of PowerShell just to show a notification is a waste of energy.

This repo exists because AI agents deserve desktop superpowers without the friction. The command line is already there — we just need it to be *nicer*.

- **One-liner APIs** instead of 10 lines of boilerplate
- **Consistent behavior** across Windows, macOS, and Linux
- **Error handling** that doesn't make you cry at 3am
- **No daemon to manage** — just files, folders, and commands that work
- **Memory that persists** — facts, workflows, and decisions survive between sessions
- **Skills on demand** — load only what you need, keep hundreds on standby

## Requirements

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

## License

MIT — do whatever, just don't blame me if your cron jobs delete your prod database.

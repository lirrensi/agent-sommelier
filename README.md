
<p align="center">
  <br>
  <img src="https://img.shields.io/badge/agent--sommelier-1.5.0-8B5CF6?style=flat-square" alt="version">
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
  <code>uv tool install "git+https://github.com/lirrensi/agent-sommelier[all]"</code>
</p>

<p align="center">
  The <code>[all]</code> extra installs every optional dependency: crony, screenshot, web UI, and MCP server.
  <br>
  <em>Not on PyPI yet — install directly from the repo.</em>
</p>

<br>

---

## Why Agent Sommelier?

You have a bare-bones app. You need to add functionality — background jobs, notifications, task tracking, memory, cron scheduling, SSH, screenshots, terminal sessions, scheduled thinking. You *could* build each one from scratch. Or you could reach for tools that already do it, do it well, and don't lock you into a workflow.

That's this repo.

**Agent Sommelier is not a workflow system.** It doesn't make plans for you, orchestrate pipelines, or prescribe how you should work. What it *does* is give you **individual, independent tools and skills** — each one a self-contained capability you can slot into anything.

- **Already have a cron setup but hate how it works?** Here's a better one.
- **Need background jobs but don't want to wrestle with `nohup`?** Grab ours.
- **Want your agent to remember things between conversations?** Memory's right here.
- **Need a task system that doesn't require a database?** Done.
- **Need to build an interactive HTML page from a conversation?** `artify`.
- **Need to send a deep-thinking model a hard question and stream the answer?** `amun`.

Pick what you need. Leave the rest. They all work independently — and they all work together when you want them to.

---

## At a Glance

### CLI tools (11)

| Command | What it does | Required extra |
|---------|--------------|----------------|
| `crony` | Cron jobs in plain English (`every 1h`, `in 30m`) | `[crony]` |
| `notify` | Cross-platform desktop notifications | built-in |
| `bg` | Background jobs, tracked by friendly name | built-in |
| `screenshot` | Screen capture, zero fuss | `[screenshot]` |
| `essh` | SSH profile manager — names, keys, filters, scp, rsync | built-in |
| `tasks` | In-repo task management with deps, queues, and a web UI | built-in (web UI needs `[web]`) |
| `tmx` | tmux/psmux session control for agents | built-in |
| `amun` | Deep-thinking LLM question-asker with streaming | built-in |
| `artify` | Build, live-serve, and snapshot interactive HTML artifacts | built-in |
| `skill-store` | On-demand skill registry — keep hundreds, load a few | built-in |
| `skill-store-mcp` | MCP server exposing the skill store to any agent | `[mcp-srv]` |

### Skills (17)

Each skill teaches an agent how to install and use the matching tool on demand — no preinstalled dependencies required.

```bash
npx skills add https://github.com/lirrensi/agent-sommelier
```

| Skill | What it gives the agent |
|-------|--------------------------|
| `bg-jobs` | Run & track background processes without tmux |
| `calm-down` | De-escalation when the agent is heading the wrong way |
| `crony` | Schedule jobs with "every 1h" instead of cron expressions |
| `desktop-notifications` | One command, three OSes |
| `document-extractor` | Convert PDFs, Office docs, media → Markdown |
| `edge-tts` | Text-to-speech via Microsoft Edge |
| `engage` | Autonomous-execution mode: build, test, verify, deliver |
| `essh` | SSH profile manager — save hosts, generate keys, filter commands |
| `memory-bank` | **Core.** Episodic, semantic, procedural memory across sessions |
| `micropatch` | Keep fork features alive across upstream updates |
| `best-practices-researcher` | Research patterns and make technology decisions |
| `batch-task-executor` | Fan-out orchestration from any task source |
| `screenshot` | Capture screens in scripts and pipelines |
| `skill-store` | Lazy-load hundreds of skills without polluting context |
| `task-system` | Full task lifecycle — deps, queues, 12 statuses, web UI |
| `tmux` | Terminal multiplexing for SSH, REPLs, parallel agents |
| `artify` | **Build & ship interactive HTML artifacts — and read their state back** |

---

## Install

```bash
uv tool install "git+https://github.com/lirrensi/agent-sommelier[all]"
```

The `[all]` extra installs every optional dependency: crony, screenshot, web UI, and MCP server. The four extras are independent and composable:

```bash
# Core only — no optional deps
uv tool install "git+https://github.com/lirrensi/agent-sommelier"

# Pick what you need
uv tool install "git+https://github.com/lirrensi/agent-sommelier[crony]"      # adds crony
uv tool install "git+https://github.com/lirrensi/agent-sommelier[screenshot]" # adds screenshot
uv tool install "git+https://github.com/lirrensi/agent-sommelier[web]"        # adds tasks web UI
uv tool install "git+https://github.com/lirrensi/agent-sommelier[mcp-srv]"    # adds skill-store-mcp
```

> **Not on PyPI yet.** Once published, swap the git URL for `agent-sommelier-cli[...]` (the package name in `pyproject.toml`).

---

## The Tools

> Each tool below is a standalone command. The deep reference for every flag, every edge case, and every platform behavior lives in [`docs/product.md`](docs/product.md). The README is the front door — it points you to the right tool, the README shows you the shape of it.

### ⏰ crony — Cron jobs, human-readable

```bash
crony add health-check "every 1h" "curl -s http://localhost:8080/health"
crony add backup      "every day at 2am" "backup.sh"
crony add reminder    "in 30m"           "notify 'Meeting' 'Starting soon!'"
crony add nightly     "0 2 * * *" "backup.sh" --cron   # raw cron if you want it
crony list && crony run health-check && crony rm backup
```

Schedules: `every 1h` · `every monday` · `at 15:30` · `in 5m` · `on 2026-03-15`. Cross-platform daemon, auto-starts on first `add`, auto-stops when empty.

### 🔔 notify — Desktop notifications

```bash
notify "Build Done" "All tests passed!"
echo "Status update" | notify "Progress"
long-task && notify "Complete" "Finished successfully"
```

Windows · macOS · Linux. One command, all three.

### ⚙️ bg — Background jobs, tracked

```bash
JOB=$(bg run "python train_model.py")   # Bash / zsh
$jobName = bg run "python train_model.py"   # PowerShell
bg status $JOB
bg logs $JOB
bg wait $JOB --match "ready"            # block until pattern appears in output
bg wait-all                            # block until everything is done
bg prune                               # nuke all terminal jobs
```

Friendly names, stable UIDs, separate record/process state, auto-prunes terminal records (keeps running jobs forever, caps terminal history at 32).

### 📸 screenshot — Screen capture

```bash
screenshot                              # auto-named with timestamp
screenshot bug_report.png               # name it yourself
path=$(screenshot) && notify "Captured" "$path"
```

Cross-platform via `mss`, with native fallbacks (`screencapture`, `gnome-screenshot`, `scrot`, `import`, `flameshot`).

### 🔑 essh — SSH profile manager

```bash
essh add deploy@192.168.1.50            # auto-name: coral-fox
essh add myserver user@host:2222
essh myserver                           # connect
essh myserver uptime                    # run a remote command
essh authorize myserver                 # gate an agent-initiated connection
essh scp myserver:/var/log/app.log ./logs/
essh rsync -avz ./build/ myserver:/srv/app/
essh filter add prod-web "rm -rf *" --action deny
essh list --json && essh export ~/backups/ssh-profiles.tar.gz
```

Names hosts, generates per-host ed25519 keys, gates agent connections with a 30-second authorization window, supports per-profile command filters (`allow`/`ask`/`deny` with wildcard matching), and ships portable tarball export/import.

### 📋 tasks — In-repo task management

```bash
tasks init
tasks add "Refactor the auth module" --tag type:refactor --priority p1
tasks add "Fix login bug" --tag type:bug --tag area:auth --priority p0
tasks next                              # top priority, unclaimed
tasks ready                             # all unblocked, sorted
tasks blocked                           # what's stuck and why
tasks overview                          # read-only dashboard, no state change
tasks take TSK-0042                     # claim it
tasks update TSK-0042 --status in-progress --notes "Found root cause"
tasks close TSK-0042 --note "Deployed to staging" --evidence "docs/release-notes.md"
tasks search login                      # full-text search
tasks history --limit 10                # recently closed
tasks serve                             # launch the web UI dashboard
```

YAML storage in `.agents/tasks/`. Statuses are config-driven — rename them, reorder them, add new ones in `meta.config`. Dependencies have teeth: `blocks`, `parent`, `child`, `discovered`, `relates`. The web UI (`tasks serve`, needs `[web]`) gives a real-time Kanban view backed by the same storage.

### 🪟 tmx — tmux/psmux session control

```bash
tmx install                             # ensure tmux/psmux is on PATH
tmx create myserver "ssh admin@prod.example.com"
tmx run myserver "hostname"             # send + wait + read
tmx run myserver "tail -100 /var/log/syslog" --timeout 10
tmx sk myserver "long-running-task"     # fire and forget
tmx r myserver                          # read full scrollback
tmx list --json
tmx rm myserver
tmx manager                             # interactive TUI picker
```

Cross-platform: `tmux` on Linux/macOS, `psmux` on Windows (auto-installed via winget/scoop/choco/cargo). Generous 10 000-line scrollback, `--json` output for every command, interactive session picker for humans.

### 🧠 amun — Deep-thinking LLM question-asker

```bash
amun init                               # write ~/.amun/config.toml
amun ask "What is the complexity of quicksort?"
amun ask "Explain the CAP theorem" --system "You are a distributed systems professor."
amun ask "Write a Python decorator" --model gpt-4o
amun ask "Write a detailed comparison" --no-stream    # rich-markdown rendering
amun doctor                             # verify config + endpoint reachability
```

Configurable OpenAI-compatible endpoint, streams by default, surfaces `reasoning`/`reasoning_content` from thinking models in dim yellow, `$ENV_VAR` references in the config. Defaults to the *"You are a senior architect and engineer. Think deeply before answering."* system prompt.

### 🎨 artify — HTML artifact build, serve, snapshot

```bash
# Offline, no server — finished artifact
artify open report.html

# Live iteration — local HTTP server with file-watch + browser auto-reload
artify serve report.html
artify serve report.html --webview      # chromeless native window

# Manage running instances
artify list                             # port, pid, file, status, url
artify kill 54321
artify restart 54321

# Read the page's state back as JSON
artify snapshot 54321 --timeout 60
```

Build a self-contained HTML file (or use one of the bundled starters), serve it locally, the browser tab auto-reloads as you save. When the page is a form, a dashboard, or any interactive surface, `artify snapshot <port>` collects its current state and prints it as JSON — the HTML becomes both the interface and the structured input. Watch a long-running serve with `bg run "artify serve report.html"`.

The full skill — artifact families, design principles, the snapshot protocol, starter catalog — lives in [`skills/artify/SKILL.md`](skills/artify/SKILL.md).

### 🧠 skill-store — On-demand skill registry

```bash
skill-store init                        # one-time setup
skill-store list                        # paginated, pinned first
skill-store search web                  # full-text search
skill-store load crony                  # show path + folder tree
skill-store preview crony               # first 100 lines
skill-store pin crony                   # move to top
skill-store create-new                  # interactive scaffolding wizard
skill-store sync                        # rebuild index, git commit
skill-store groups create backend ...   # organize skills into groups
```

A local registry for agent skills. Keep hundreds on disk, load only what you need into context. The companion **`skill-store-mcp`** (extra `[mcp-srv]`) exposes a read-only subset (`search_skills`, `get_skill`, `preview_skill`, `list_skills`) to any MCP-aware agent — wire it into `opencode.json` and the agent can browse the registry on its own.

---

## The Skills

Skills are how an agent installs and uses a tool on demand. They are Markdown files under `skills/<name>/SKILL.md` plus optional `references/`, `scripts/`, `starters/`, and `templates/` subfolders. The agent reads the SKILL.md, follows the install path, and uses the tool.

Two skills deserve a special callout:

- **`memory-bank`** is the **core** skill. It keeps your agent's context alive across sessions — episodic, semantic, and procedural memory types, an auto-maintained `INDEX.md` with tag search, Obsidian-compatible templates. Run `bat ./memory/INDEX.md` to orient yourself when resuming work.
- **`engage`** is the **autonomous-execution trigger**. When the user says "engage", "go autonomous", "execute the plan", or "make it happen", the agent switches to plan → build → test → verify → deliver with no questions asked until completion.

Other skills worth knowing: `task-system` for the in-repo task CLI, `skill-store` for lazy-loading hundreds of skills, `tmux` for terminal session control, `best-practices-researcher` for tech-stack decisions, `micropatch` for surviving upstream changes in a fork, `document-extractor` for turning PDFs and Office docs into Markdown, and `artify` for when the right output is a browser page instead of a wall of text.

Full inventory and behavior of every skill is in [`docs/product.md`](docs/product.md#skills).

---

## For AI Agents

This repo is designed *for* agents. Each tool can be self-installed on demand using the included skills:

```
skills/
├── artify/                 # HTML artifacts, live reload, snapshot back
├── batch-task-executor/    # fan-out work from any task source
├── best-practices-researcher/   # patterns + tech decisions
├── bg-jobs/                # background jobs without tmux
├── calm-down/              # de-escalation
├── crony/                  # human-readable cron
├── desktop-notifications/  # notify across OSes
├── document-extractor/     # PDF/Office/media → Markdown
├── edge-tts/               # text-to-speech
├── engage/                 # autonomous-execution mode
├── essh/                   # SSH profiles + filters
├── memory-bank/            # persistent cross-session memory
├── micropatch/             # fork-feature survival
├── screenshot/             # screen capture
├── skill-store/            # lazy-load hundreds of skills
├── task-system/            # tasks CLI, lifecycle, web UI
└── tmux/                   # terminal session control
```

The pattern:

1. Agent checks if tool exists: `crony --help`
2. If not, installs it: `npx skills add https://github.com/lirrensi/agent-sommelier`
3. Uses it

No MCP servers required for the core experience. No OAuth. No config files. Just tools that agents can reach for.

---

## Repository Map

```
agent-sommelier/
├── src/agent_sommelier/             # CLI tool implementations (Python)
│   ├── notify.py                    # desktop notifications
│   ├── bg.py                        # background job manager
│   ├── crony/                       # cron job scheduler (CLI + daemon)
│   ├── essh.py                      # portable SSH wrapper
│   ├── screenshot.py                # screen capture
│   ├── tasks/                       # in-repo task management package
│   │   ├── cli.py                   #   `tasks` entry point
│   │   ├── core.py / storage.py / render.py
│   │   └── web/                     #   `tasks serve` web UI (FastAPI + Vite/TS)
│   ├── skill_store/                 # on-demand skill registry
│   │   ├── cli.py                   #   `skill-store` entry point
│   │   └── mcp.py                   #   `skill-store-mcp` MCP server
│   ├── tmx.py                       # tmux/psmux session control
│   ├── amun.py                      # deep-thinking LLM question-asker
│   └── artify.py                    # HTML artifact serve/snapshot
├── skills/                          # 17 agent skill definitions
├── docs/                            # product.md, spec.md, arch.md
├── tests/                           # pytest suite
├── pyproject.toml                   # package metadata + 11 entry points
├── uv.lock                          # locked dependencies
└── README.md
```

---

## Requirements

- **Python 3.10+**
- **[uv](https://github.com/astral-sh/uv)** (recommended) or pip
- Optional system tools used by specific commands (the tools guide you when they are missing):
  - `tmux` or `psmux` (for `tmx`) — auto-installed on Windows via `tmx install`
  - `scp`, `rsync` (for `essh scp` / `essh rsync`)
  - `notify-send` (Linux for `notify`)
  - `libnotify` (Linux for desktop notifications)

---

## Where to read next

| Doc | What it covers |
|---|---|
| [`docs/product.md`](docs/product.md) | Full reference for every tool: every command, every flag, every edge case, every platform behavior |
| [`docs/spec.md`](docs/spec.md) | The contract every tool honors (exit codes, file formats, retention policy) |
| [`docs/arch.md`](docs/arch.md) | How the pieces fit together: storage layouts, IPC mechanisms, the daemon model |
| `skills/<name>/SKILL.md` | Per-skill install + usage instructions for agents |
| `private/` | Working notes, design docs, phase plans (not part of the public package) |

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

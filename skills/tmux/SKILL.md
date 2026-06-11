---
name: tmux
description: >
  Control tmux/psmux sessions for interactive CLIs, SSH connections, and parallel agent orchestration.
  Works cross-platform: tmux on Linux/macOS, psmux on Windows. Provides sync commands that send keys
  and automatically capture output. Triggers: "run in tmux", "create tmux session", "tmux", "SSH session",
  "parallel terminals", "run multiple agents".
---

# tmux Skill

Control tmux sessions programmatically. Send keys, capture output, run interactive processes.

**Cross-platform:**
- **Linux/macOS**: Uses native `tmux`
- **Windows**: Uses `psmux` (native Windows tmux, 95%+ syntax compatible)

**Use tmux when:**
- SSH to remote servers (persistent sessions)
- Interactive TTY needed (REPLs, shells, prompts)
- Running multiple processes in parallel
- Orchestrating coding agents

**Don't use tmux for:**
- Simple background tasks → use `bg run` instead
- One-shot commands → run directly

---

## Helper: tmx

**Primary:** Python CLI tool (`tmx`) — installable via agent-sommelier-cli.
**Fallback:** Shell scripts at `skills/tmux/scripts/tmx.sh` (bash) and `tmx.ps1` (PowerShell).

### Commands

| Command | Description |
|---------|-------------|
| `tmx install` | Ensure tmux/psmux is available (auto-install on Windows) |
| `tmx create <name> [cmd]` | Create session, optionally run init cmd |
| `tmx rm <name>` | Kill session |
| `tmx sk <name> "<cmd>"` | Send keys (fire and forget) |
| `tmx r <name>` | Read output (full scrollback) |
| `tmx run <name> "<cmd>" [--timeout N]` | Send + wait N seconds + read output |
| `tmx list [--json]` | List all sessions (rich table or JSON) |
| `tmx manager` | Interactive session picker |

---

## Quick Start

```bash
tmx install
tmx create mysession
tmx run mysession "ls -la"
tmx r mysession
tmx rm mysession
```

```powershell
tmx install
tmx create mysession
tmx run mysession "Get-ChildItem"
```

---

## Pattern 1: SSH Sessions (Most Common)

```bash
# Create SSH session
tmx create server "ssh user@myserver.com"
tmx run server "hostname" --timeout 3
tmx sk server "mypassword"
tmx run server "tail -100 nginx/access.log" --timeout 5
tmx r server
```

## Pattern 2: Background Process Monitoring

```bash
tmx create api "cd /app && python api.py"
tmx r api | grep -q "Listening" && echo "API is up"
tmx create db "docker run --name postgres postgres"
tmx list
```

## Pattern 3: Interactive REPL

```bash
tmx create python "python3 -q"
tmx run python "import sys; print(sys.version)"
tmx run python "2 + 2" --timeout 3
```

## Pattern 4: Parallel Agents

```bash
for i in 1 2 3; do
  tmx create "agent-$i" "cd /tmp/project$i && codex --yolo 'Fix bugs'"
done
tmx list
tmx r agent-1
```

---

## Key Bindings (when attached)

| Key | Action |
|-----|--------|
| `Ctrl+b d` | Detach from session |
| `Ctrl+b c` | Create new window |
| `Ctrl+b n/p` | Next/Previous window |
| `Ctrl+b %` | Split vertical |
| `Ctrl+b "` | Split horizontal |

---

## Deeper Reading

| Topic | File |
|---|---|
| Installation per-platform, platform differences, gotchas | [`references/setup.md`](references/setup.md) |

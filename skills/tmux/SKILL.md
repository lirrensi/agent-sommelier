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

## Installation

### Linux/macOS
```bash
# Debian/Ubuntu
sudo apt install tmux

# macOS
brew install tmux
```

### Windows (psmux)

psmux is a native Windows tmux implementation. Same commands, same config.

```powershell
# WinGet (recommended)
winget install psmux

# Scoop
scoop bucket add psmux https://github.com/marlocarlo/scoop-psmux
scoop install psmux

# Chocolatey
choco install psmux

# Cargo
cargo install psmux
```

**If installation fails:**
- Check GitHub releases: https://github.com/marlocarlo/psmux/releases
- Download `.zip`, extract, add to PATH
- Requires PowerShell 7+ (install: `winget install Microsoft.PowerShell`)

**Verify:**
```powershell
psmux -V
# or just: tmux (psmux ships with tmux alias)
```

---

## Quick Start

```bash
# Ensure tmux/psmux is available
tmx install

# Create a session and run commands
tmx create mysession
tmx run mysession "ls -la"
tmx r mysession
tmx rm mysession
```

```powershell
# Windows — tmx install handles psmux automatically
tmx install
tmx create mysession
tmx run mysession "Get-ChildItem"
tmx r mysession
tmx rm mysession
```

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
| `tmx manager` | Interactive session picker (arrow-key TUI for humans) |

### Sync (send + wait + read)

`tmx run` uses a fixed timeout (default 5s, configurable via `--timeout` or `-t`).
No prompt regex, no fragile waiting — just send, wait, and capture.

```bash
tmx run server "pip install numpy"
tmx run server "make build" --timeout 30
tmx run server "python script.py" -t 10
```

---

## Pattern 1: SSH Sessions (Most Common)

SSH through tmux gives persistent sessions that survive disconnects.

```bash
# Create SSH session
tmx create server "ssh user@myserver.com"

# Wait a moment for SSH to connect, then check
tmx run server "hostname" --timeout 3

# Send password (if needed)
tmx sk server "mypassword"

# Run remote commands
tmx run server "cd /var/log && ls -la"
tmx run server "tail -100 nginx/access.log" --timeout 5

# Long-running remote task (fire and forget)
tmx sk server "docker logs -f mycontainer"
# Later: read output
tmx r server

# Detach and return later - SSH persists!
tmx list  # session still there
tmx r server  # get latest output
```

**SSH with sudo:**
```bash
tmx create prod "ssh admin@prod-server"
tmx sk prod "sudo_password"
tmx run prod "tail -100 /var/log/syslog" --timeout 5
```

---

## Pattern 2: Background Process Monitoring

Monitor long-running processes in tmux.

```bash
# Start a server
tmx create api "cd /app && python api.py"
tmx run api "echo 'Server starting...'" --timeout 3

# Check if running
tmx r api | grep -q "Listening" && echo "API is up"

# Monitor logs
tmx r api

# Multiple services
tmx create db "docker run --name postgres -e POSTGRES_PASSWORD=pass postgres"
tmx create redis "redis-server"

# Check all
tmx list
tmx r db
tmx r redis
```

---

## Pattern 3: Interactive REPL

Use `tmx run` with appropriate timeout for REPL interaction.

```bash
# Python (standard REPL)
tmx create python "python3 -q"
tmx run python "import sys; print(sys.version)"
tmx run python "2 + 2" --timeout 3

# Node.js
tmx create node "node -i"
tmx run node "1+1" --timeout 3
```

---

## Pattern 4: Parallel Agents

```bash
# Spin up multiple coding agents
for i in 1 2 3; do
  tmx create "agent-$i" "cd /tmp/project$i && codex --yolo 'Fix bugs'"
done

# Poll for completion
for s in agent-1 agent-2 agent-3; do
  if tmx r "$s" | grep -q "completed"; then
    echo "$s: DONE"
  fi
done

# Get results
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
| `Ctrl+b [` | Copy mode (scroll) |

---

## Platform Differences

| Feature | tmux (Unix) | psmux (Windows) |
|---------|-------------|-----------------|
| Sessions | ✅ | ✅ |
| Windows/Panes | ✅ | ✅ |
| Detach/Attach | ✅ | ✅ |
| `capture-pane` | ✅ | ✅ |
| `send-keys` | ✅ | ✅ |
| `.tmux.conf` | ✅ | ✅ (also `.psmux.conf`) |
| Mouse support | ✅ | ✅ |
| SSH mouse | ✅ | Win11 22H2+ |

---

## Gotchas

- **Timeout-based sync**: `tmx run` uses a fixed timeout (default 5s). For long commands, increase with `--timeout 30`.
- **Named sessions only**: No socket complexity — just session names.
- **Initial command on create**: `tmx create server "ssh user@host"` runs the command immediately. Use for SSH, REPLs, and services.
- **No attach**: tmx is agent-focused — no TTY attach capability. Use `tmux attach -t <name>` directly if needed.
- **psmux is fresh**: If issues, check GitHub releases for updates.
- **Windows needs PowerShell 7+**: Install with `winget install Microsoft.PowerShell`.

---

## Raw Commands

```bash
# Target syntax: session:window.pane
tmux send-keys -t mysession -l -- "echo hello"
tmux send-keys -t mysession Enter
tmux capture-pane -p -J -t mysession -S -500
tmux list-sessions
```

# Setup & Platform Details

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

## Gotchas

- **Timeout-based sync**: `tmx run` uses a fixed timeout (default 5s). For long commands, increase with `--timeout 30`.
- **Named sessions only**: No socket complexity — just session names.
- **Initial command on create**: `tmx create server "ssh user@host"` runs the command immediately.
- **No attach**: tmx is agent-focused — no TTY attach capability. Use `tmux attach -t <name>` directly if needed.
- **psmux is fresh**: If issues, check GitHub releases for updates.
- **Windows needs PowerShell 7+**: Install with `winget install Microsoft.PowerShell`.

## Raw Commands

```bash
# Target syntax: session:window.pane
tmux send-keys -t mysession -l -- "echo hello"
tmux send-keys -t mysession Enter
tmux capture-pane -p -J -t mysession -S -500
tmux list-sessions
```

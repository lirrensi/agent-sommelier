---
name: essh
description: >
  Portable SSH profile manager for agents. Run remote commands on saved hosts
  by friendly name instead of typing user@host -i key every time. Type less
  crap around your SSH commands.
---

# ESSH Skill

Abstracts SSH so you type less crap around your commands.

The human sets up profiles (`essh add ...`). You just **use** them.

## Installation Check

```bash
essh --help
```

If not installed:
```bash
uv tool install "git+https://github.com/lirrensi/agent-sommelier"
```

## Usage

### List available profiles
```bash
essh list --json
```

### Run a command on a saved host
```bash
essh <name> <command>
```

That's it. No `-i key.pem`, no `user@host:22`. Just the name.

## Examples

```bash
essh list --json
# → [{"name": "coral-fox", "user": "deploy", "host": "192.168.1.50", "port": 22, "key_path": ""}]

essh coral-fox "uname -a"
essh coral-fox "systemctl status nginx"
essh prod-web "tail -n 100 /var/log/app.log"

## Command Filters
The human may set up command filter rules to control what you can run on
each host. Filters use wildcard matching with three actions:

- **allow** — The command runs without any prompting.
- **ask** — The command is blocked until the human authorizes it (same
  authorization flow as the connection itself).
- **deny** — The command is rejected immediately with an error message.
  There is no way to bypass this.

### Checking if filters are defined
```bash
# Global filters (apply to all hosts)
essh filter list global
# Per-host filters (apply to a specific host)
essh filter list <name>
```

### How filters affect you
1. When you run `essh <name> "<command>"`, essh checks all filter rules (global first, then per-host) from top to bottom. The **last matching** rule wins.

2. If a **deny** rule matches: the command is rejected. You will see something like:

   ```
   ❌ BLOCKED: This command is blocked by a filter rule.
     Command: rm -rf /etc
   ```

3. If an **ask** rule matches: in non-TTY mode, essh creates an authorization request showing the exact command. The human sees:

   ```
   Pending request for 'prod-web':
     Command: rm -rf /var/log
   ```

   They must run `essh authorize <name>` for the command to proceed.

4. If an **allow** rule matches, or no rule matches: the command runs normally through the existing authorization gate.

### Pattern syntax
The same wildcard rules as opencode (anomalyco) permission system:

| Pattern | Matches | Doesn't match |
|---|---|---|
| `rm *` | `rm`, `rm -rf /` | `rmdir` |
| `shutdown *` | `shutdown`, `shutdown -h now` | `shutdown` with no args? yes, actually |
| `git *` | `git`, `git status` | (anything starting with git) |
| `rm -rf *` | `rm -rf /`, `rm -rf .` | `rm -r` |

Key rule: `*` in patterns like `rm *` makes the trailing space and arguments **optional** — so `rm *` matches both bare `rm` and `rm -rf /`.

If you get blocked and think it's a mistake, tell the human:

```
The command "..." was blocked by a filter rule.
You may need to add an ``--action allow`` rule or adjust the pattern.
```
```

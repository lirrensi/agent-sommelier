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
```

# Installing the `skill-store` CLI

## With `uv` (recommended)

```bash
# Install from PyPI
uv tool install agentcli-helpers

# Or install from git (latest)
uv tool install git+https://github.com/lirrensi/agent-cli-helpers
```

## With `pip`

```bash
pip install agentcli-helpers
```

## Verify

```bash
skill-store --version
skill-store list
```

## First-time Setup

```bash
skill-store init    # creates ~/.skill-store/ + index.json + git
```

Run once. Never think about it again.

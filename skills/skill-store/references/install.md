# Installing the `skill-store` CLI

## With `uv` (recommended)

```bash
# Install from git (primary)
uv tool install "git+https://github.com/lirrensi/agent-sommelier"

# Or install from PyPI (when published)
# uv tool install agent-sommelier-cli
```

## With `pip`

```bash
# pip install agent-sommelier-cli    # Uncomment when published
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

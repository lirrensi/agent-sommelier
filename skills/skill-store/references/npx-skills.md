# `npx skills` for `skill-store`

## ⚠️ YOU MUST RUN FROM `~/.skill-store/`

`npx skills` installs skills **relative to your current directory**.

If you run it from anywhere other than `~/.skill-store/`, skills will land
in the wrong place and `skill-store` won't find them.

```bash
# ✓ CORRECT — navigate to the store first
cd ~/.skill-store
npx skills add <source> -a openclaw --copy -y
skill-store sync

# ✗ WRONG — runs from wherever you happen to be
npx skills add <source> -a openclaw --copy -y
```

**This is the #1 cause of "skill not found" errors.** Always `cd` first.

---

## The One Workflow

```bash
cd ~/.skill-store
npx skills add <source> -a openclaw --copy -y
skill-store sync
```

### Flags explained

| Flag | Purpose |
|---|---|
| `-a openclaw` | Target OpenClaw so skills land in `skills/<name>/` instead of `.agents/skills/` |
| `--copy` | Copy files directly (avoids creating `.agents/skills/` canonical copy) |
| `-y` | Skip confirmation prompts |

### Anti-patterns

```bash
# ✗ DON'T — wrong directory
npx skills add vercel-labs/agent-skills -a openclaw --copy -y

# ✗ DON'T — installs to .agents/skills/ (wrong dir)
npx skills add vercel-labs/agent-skills

# ✗ DON'T — installs globally outside the store
npx skills add vercel-labs/agent-skills -g

# ✓ DO — from correct dir with correct flags
cd ~/.skill-store
npx skills add vercel-labs/agent-skills -a openclaw --copy -y
skill-store sync
```

---

## Why `-a openclaw --copy`

`npx skills` supports 55+ agents. Each has a different project path:

| Agent type | Project path |
|---|---|
| **Universal** (Amp, Cline, Codex, Cursor, etc.) | `.agents/skills/` |
| **OpenClaw** | `skills/` |

Your `skill-store` uses `skills/<name>/`. OpenClaw's path matches.
Universal agents' paths do not.

`--copy` makes the skill write directly into `skills/<name>/`. Without it,
`npx skills` creates a canonical copy in `.agents/skills/` and symlinks from
`skills/` — more clutter.

---

## Other Operations

```bash
# Update all installed skills
cd ~/.skill-store
npx skills update -y
skill-store sync

# Remove a skill
cd ~/.skill-store
npx skills remove <slug> -a openclaw -y
skill-store sync

# List what's installed
npx skills list
```

---

## Troubleshooting

**"Skill not found after install"** → Did you `cd ~/.skill-store` first?
Then run `skill-store sync`.

**"Files ended up in `.agents/skills/`"** → You forgot `-a openclaw`.
Fix:
```bash
mv ~/.skill-store/.agents/skills/<slug> ~/.skill-store/skills/<slug>
rm -rf ~/.skill-store/.agents
skill-store sync
```

---

## Reference Links

- [npm package: skills](https://www.npmjs.com/package/skills)
- [Skills Registry](https://skills.sh)
- [Agent Skills Specification](https://github.com/vercel-labs/agent-skills)

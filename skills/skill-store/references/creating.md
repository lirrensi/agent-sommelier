# Creating a New Skill

## First-time Setup

```bash
skill-store init    # one-time: creates ~/.skill-store/ + index.json + git
```

## Create

```bash
skill-store create-new
```

Prompts for slug (kebab-case, unique), name, description. Creates the folder and
a templated `SKILL.md`.

## Edit

Edit files in `~/.skill-store/skills/<slug>/`.

## Sync

```bash
skill-store sync    # rebuild index so search/list pick up changes
```

## Skill Format

```
<slug>/
├── SKILL.md              # Required. YAML frontmatter + markdown body
│   ├── name: string
│   └── description: string
├── scripts/              # Optional. Executable helpers
├── references/           # Optional. Docs loaded on demand
└── assets/               # Optional. Templates, icons, etc.
```

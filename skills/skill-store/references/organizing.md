# Organizing Your Skill Store

A flat list stops scaling around 30 skills. Groups give you namespaces.

## Quick Check — What's Unorganized?

```bash
skill-store groups organize-helper
```

Shows every skill not yet in a group. Run it after each organizing batch until it prints the happy checkmark.

## Workflow

### 1. Decide Your Autonomy Level

You can dial how much you want to be bothered. Three levels:

| Level | What happens | Best for |
|---|---|---|
| **Interactive** | Agent asks before creating every group and assigning every skill. Zero surprises. | <20 skills, or you are a taxonomy perfectionist |
| **Plan-then-Execute** (recommended) | Agent proposes the group structure and a few example assignments. You approve the plan. Agent then runs the rest without asking. | 20–100 skills; you care about top-level buckets but not micro-managing every slug |
| **YOLO** | Agent scans, designs taxonomy, creates groups, and assigns everything in one shot. Reports what it did at the end. | 100+ skills, bulk import, or you trust the agent's judgment |

Default to **Plan-then-Execute**. It gives you veto power over the big decisions without making you approve 150 individual `groups add` commands.

### 2. If You Have Zero Groups

Run `skill-store groups organize-helper` to see the full orphan list.

Then scan the slugs and descriptions. Propose 4-8 top-level groups. Good defaults:

- `automation` — cron, batch jobs, background workers
- `devops` — Docker, CI/CD, deploy scripts
- `communication` — Slack, email, notifications
- `data` — parsing, extraction, conversion
- `testing` — QA, browser automation, evals
- `productivity` — task systems, notes, search
- `integration` — APIs, webhooks, third-party tools

Create them:

```bash
skill-store groups create devops "DevOps & Infra" "CI/CD, containers, deploy"
```

Then start filing skills. Use the helper after each batch to watch the count drop.

**Autonomy note:** In *Plan-then-Execute* mode, approve the group list once, then let the agent file the rest. In *YOLO* mode, it creates groups and files everything automatically — you review after.

### 3. If You Already Have Groups

Run `skill-store groups list` to see your current structure.

Run `skill-store groups organize-helper` to see orphans.

**Try to fit into existing groups first.** Only propose a new group if:
- A skill clearly doesn't belong in any existing group, **and**
- You can name at least 2 other skills (present or future) that would share that category

If both are true, create the group, then assign.

**Autonomy note:** In *Plan-then-Execute*, the agent will show you the proposed fits for the first few orphans, ask if the pattern looks right, then batch the rest. In *YOLO*, it does a full scan and reports the new groups it created and any controversial assignments.

### 4. Batch Commands

Add multiple skills at once:

```bash
skill-store groups add devops docker-helper deploy-script
```

Remove a misfit:

```bash
skill-store groups rm devops docker-helper
```

## Key Decisions

1. **Groups are cheap.** Creating a group costs nothing. Prefer creating a narrow group over stuffing unrelated skills into a broad one.
2. **A skill can only live in one group.** This is intentional — it forces you to decide the primary domain. If a skill truly spans two groups, pick the one you'd search first.
3. **Don't organize for the sake of it.** If you have <15 skills, a flat list with pins is often faster. Groups shine at 30+.
4. **Iterate.** Your first grouping won't be perfect. The helper command exists precisely so you can chip away at it in 5-minute sessions.
5. **Autonomy is a dial, not a switch.** Start with *Plan-then-Execute* for your first big organization. Switch to *YOLO* for maintenance (new skills arriving weekly). Drop to *Interactive* only when the agent keeps putting things in the wrong bucket.

## Example Sessions

### Plan-then-Execute (recommended)

```text
User: "Organize my store, plan-then-execute mode"

Agent: "You have 47 ungrouped skills. I propose these groups:
  - automation (cron-batch, skill-creator, bg-jobs)
  - data (document-extractor, markitdown)
  - devops (docker-helper, deploy-script)
  - communication (desktop-notifications, edge-tts)
  - 43 others I can auto-assign.
  Approve the group list?"

User: "yes, but rename 'data' to 'data-tools'"

Agent: "Done. Filing the remaining 43 skills..."
[...runs all groups add commands...]
"All organized. 0 orphans remaining."
```

### Interactive

```text
User: "Organize my store, interactive mode"

Agent: "Skill 'cron-batch' — create group 'automation' and add it?"
User: "yes"
Agent: "Skill 'document-extractor' — add to 'automation' or create new group?"
User: "create 'data-tools'"
[...continues for every skill...]
```

### YOLO

```text
User: "Organize my store, YOLO mode"

Agent: "Scanning 47 skills... Created 5 groups, assigned everything.
  Groups: automation (12), data-tools (8), devops (9), communication (7), productivity (11).
  Review with: skill-store groups list && skill-store groups organize-helper"
```

## See Also

- `skill-store groups --help`
- `skill-store status` — store health dashboard

# How to Operate — task-system

## Starting a Session

```bash
# 1. Check what's pending
tasks status

# 2. See what's unblocked and actionable
tasks ready

# 3. Or see full queue (including blocked)
tasks next --take all

# 4. Check for blockers if things feel stuck
tasks blocked
```

## Inbox Processing

The most important multi-step operation. Turn raw notes into structured tasks, then leave the inbox empty.

```bash
# 1. Read everything in the inbox — don't skip items
tasks inbox

# 2. Draft tasks for every actionable item
#    For each item, ask: "Is this clear enough to execute without further input?"
#    If unclear → ask the user clarifying questions BEFORE creating the task
#    If clear  → draft with title, notes, priority, tags

tasks add "Fix login timeout on mobile" --source inbox --priority high --tag bug --notes "Must reproduce on iOS Safari and Android Chrome"
tasks add "Update README with API examples" --source inbox --priority low --tag docs --notes "Add curl examples for all 5 endpoints"

# 3. Present the draft tasks to the user for confirmation
#    "I understood your inbox as 4 tasks. Here's what I'll create: [...] Proceed?"
#    Only create tasks after explicit confirmation.

# 4. Clear the inbox completely — leave it empty
echo. > .agents/tasks/inbox.md          # Windows: wipe file to blank
# > .agents/tasks/inbox.md              # Unix: wipe file to blank
```

**Ingress Rules:**
- **Take ALL items**, not just the easy ones
- **Ask clarifying questions** when an item is vague, ambiguous, or missing context. The goal is to make tasks as complete as possible — but if the user chooses not to answer or hasn't thought it through yet, create the task anyway with whatever context is available.
- **Get confirmation** before creating tasks and wiping the file
- After processing, the inbox must be **empty** — not partially cleaned, not annotated, not "organized." Empty.
- If an item is unclear and the user is unavailable, **leave it in the inbox** and process what you can

> **Default behavior:** Wipe the inbox clean after processing. Only skip wiping if the user explicitly says to keep it. When in doubt, empty it.

## Creating Work

```bash
# Quick task from a known priority
tasks add "Fix the login redirect" --priority p1 --tag bug

# Task from a detailed inbox note
tasks add "Implement rate limiting" --source inbox --priority medium --notes "See inbox.md for requirements"

# Task that depends on something else
tasks add "Deploy to production" --dep TSK-0007:blocks --priority p0

# Task with a soft relationship
tasks add "Investigate edge case" --related TSK-0002
```

## Tracking Progress

```bash
# Start working
tasks update TSK-0004 --status in-progress

# Hit a blocker — mark it
tasks update TSK-0004 --status blocked --notes "Waiting on API key from IT"
tasks update TSK-0004 --dep TSK-0010:blocks  # record what's blocking it
# Later: unblocked
tasks update TSK-0004 --status in-progress

# Needs review before shipping
tasks update TSK-0004 --status review

# Priority changed — push it to later
tasks update TSK-0004 --status postponed --priority p3

# Put it in the backlog for future triage
tasks update TSK-0004 --status backlog

# Something came up — change priority, add context
tasks update TSK-0004 --priority p0 --notes "Client escalation, needs immediate attention"

# Cancelled — no longer relevant
tasks update TSK-0004 --status cancelled --notes "Requirement dropped"

# Close it — could be cancelled, abandoned, or finished
tasks close TSK-0004 --note "Deployed to prod, monitors green"
```

## Moving Between Statuses

No restrictions on status transitions:

```bash
# Direct skip: backlog → done
tasks update TSK-0010 --status done

# Backwards: review → todo (more work needed)
tasks update TSK-0011 --status todo --notes "Review found issues, needs rework"

# Dead end: in-progress → abandoned
tasks update TSK-0012 --status abandoned --notes "Proof of concept failed"
```

The only special status is `done` — use `tasks close TSK-NNNN` to move it to the archive.
Use `tasks update TSK-NNNN --status done` only when backdating or correcting a closed task's status.

## Reviewing

```bash
# What's on the plate
tasks list --status todo

# What's been accomplished
tasks history --json

# Full session overview
tasks status
```

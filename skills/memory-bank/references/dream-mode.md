# Dream Mode Reference

Use this when the agent is asked to **"dream"**, do a **background memory pass**, or otherwise focus on **retrieving and distilling prior context instead of mainly reacting to the current conversation**.

This is **not limited to cron**. It also fits:
- background jobs
- nightly maintenance loops
- manual review passes
- end-of-day consolidation
- long-running app sessions that periodically distill context

## What Dream Mode Is

Dream mode is **not** a separate memory type and **not** a special note template.

It is a **shortcut reference / operating mode** for using the normal memory-bank system with a different emphasis:

- less focus on the immediate turn
- more focus on retrieval from prior records
- more focus on consolidation across many prior sessions or artifacts
- same memory types as always: `episodic`, `semantic`, `procedural`
- same normal templates as always

If the user says things like:
- "dream about this"
- "run a background memory pass"
- "consolidate what we've learned"
- "review prior sessions and update memory"

...then **read this file first**, and then proceed with the ordinary memory-bank workflow.

## Goal

Dream mode is a **maintenance and enrichment pass** over prior material.

It should:
- review what happened
- extract durable truths
- improve reusable procedures
- preserve meaningful history
- avoid creating duplicate semantic/procedural files
- leave a trace of what it processed

## One-line rule

**Episodic records what happened during the pass. Semantic and procedural are updated like wiki pages.**

## Inputs Dream Mode May Use

Use whatever tools are available in the runtime, but prefer this order of operations:

1. **Read the memory map first**
   - `./memory/INDEX.md`
2. **Read relevant existing memories**
   - especially `semantic/` and `procedural/` before creating anything new
3. **Read source material for consolidation**
   - previous session logs
   - notes
   - transcripts
   - task records
   - summaries
   - app-specific history
4. **Read prior dream/maintenance records**
   - to avoid reprocessing the same material blindly

## Recommended Procedure

### 1) Orient

- Read `./memory/INDEX.md`
- Scan existing semantic/procedural files related to the topic
- Check whether a maintenance log already exists for today

### 2) Determine the source window

Define what this dream pass is reviewing, for example:
- "since last successful dream run"
- "today's sessions"
- "the last 24 hours"
- "unprocessed transcripts in folder X"

If possible, record the exact scope.

### 3) Track what is being processed

Keep a small record of:
- inputs reviewed
- time window covered
- items fully processed
- items skipped or deferred
- confidence or open questions

Recommended optional location:
- `./memory/maintenance/`

Suggested files:
- `./memory/maintenance/dream_runs/YYYY_MM_DD.md`
- `./memory/maintenance/processing_status.md`

If a machine-readable format helps the host app more, a parallel `.jsonl` or `.json` log is also fine.

This tracking record is optional support material, **not a new memory type**.

### 4) Extract memory by type

For each important piece of information, ask:

- **What happened?** -> create or update `episodic`
- **What is now true?** -> update existing `semantic`, or create one if the topic is genuinely new
- **How should this be done next time?** -> update existing `procedural`, or create one if genuinely new

### 5) Update episodic carefully

Use episodic for:
- what the reviewed sessions contained
- important incidents, discoveries, decisions, failures, milestones
- the fact that a dream pass happened, if the pass itself is worth preserving

Guidance:
- prefer dated files
- if multiple dream passes run on the same day, updating that day's dream-related episodic note is acceptable **when the scope is the same ongoing maintenance pass**
- create a new episodic file when the run is materially separate in purpose, source window, or significance

### 6) Update semantic aggressively but cleanly

Use semantic for:
- stable preferences
- durable facts
- current state
- constraints
- profiles

Guidance:
- search for an existing subject file first
- update the existing file if it already represents the topic
- create a new file only when the topic does not yet have a stable home
- do not create "one semantic file per session"

### 7) Update procedural when patterns repeat

Use procedural for:
- repeatable workflows
- checklists
- behavior rules
- response patterns
- maintenance routines

Guidance:
- refine the canonical how-to/playbook
- add lessons learned from repeated sessions
- avoid creating a new procedural file for every tiny tweak

### 8) Mark uncertainty honestly

If something is tentative:
- use `confidence: tentative` or `likely`
- preserve the source in `source:`
- avoid overstating a pattern from one weak example

### 9) Rebuild the map

After updates:

```bash
python skills/memory-bank/scripts/index.py
```

## What To Create During Dream Mode

Dream mode does **not** introduce new file types.

Create or update only the normal things memory-bank already uses:

- `episodic` notes for events, reviewed windows, notable discoveries, or meaningful consolidation passes
- `semantic` notes for durable truths and current state
- `procedural` notes for repeatable workflows and behavior guidance
- optional maintenance/processing records if the host environment benefits from them

If you need structure, reuse the normal memory-bank templates and naming rules.

## Good Defaults

If the runtime gives you very little structure, default to this:

1. read `INDEX.md`
2. identify the unreviewed source window
3. update existing semantic/procedural files first
4. create episodic notes for notable events or the dream pass itself
5. leave a maintenance record showing what was processed
6. rebuild `INDEX.md`

## Anti-Patterns

Avoid these little gremlins:

- creating a fresh semantic file every run
- creating a fresh procedural file for every minor refinement
- overwriting meaningful historical episodic context
- processing the same source repeatedly with no marker or ledger
- claiming durable truth from a single weak signal
- editing `INDEX.md` by hand instead of rebuilding it

# Implementation Plan: hard-bound bg launch

## Overview
Make `bg run` impossible to block indefinitely.

Behavior target:
- `bg run` always returns or fails within 10 seconds
- launch remains best-effort and may return a PID when available
- any shell/launcher weirdness must not leave the CLI stuck waiting

## Changes

### 1. Add a hard launch timeout
Wrap the process launch path in `launch_process_for_job(...)` with a 10-second deadline.
- Bound the Windows launcher hop
- Bound the Unix `Popen` path too, even though it should be fast
- If the child/launcher does not finish in time, treat it as launch failure and clean up

### 2. Separate “launch confirmation” from “job runtime”
Keep `bg run` synchronous only until it can confirm launch details.
- If PID is captured, persist it and return immediately
- If PID cannot be confirmed before the deadline, fail fast instead of waiting

### 3. Make failures non-sticky
If launch fails or times out:
- remove the partially-created record/index entry
- avoid leaving a half-open job that looks running
- surface a clear CLI error

### 4. Update user-facing docs
Align `docs/product.md`, `docs/arch.md`, and `README.md` / `skills/bg-jobs/SKILL.md` with the hard 10-second launch guarantee.

## Files to modify
- `src/agentcli_helpers/bg.py`
- `docs/product.md`
- `docs/arch.md`
- `README.md`
- `skills/bg-jobs/SKILL.md`

## Verification
- Run a focused `bg run` smoke test against a fast command
- Run a deliberately awkward launch case and confirm the CLI still returns within 10 seconds
- Check that successful launches still persist PID / metadata normally

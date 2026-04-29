# Implementation Plan: immediate bg fire-and-forget launch

## Goal
Make `bg run` return immediately after creating the job handle, without waiting for launch confirmation.

## Behavior target
- `bg run` prints the handle right away.
- The actual process launch happens in a detached worker.
- If the worker or target launch fails, the record remains queryable and is marked failed instead of being deleted.
- A job may briefly appear as launching/starting before the target PID is known.

## Changes

### 1. Split synchronous launch from worker launch
Keep the current platform-specific launch logic in a helper used by a detached worker process.
- Main `bg run` should not call `subprocess.run(..., timeout=...)`.
- Main `bg run` should spawn a detached Python worker with `Popen` and return immediately.

### 2. Preserve the handle on launch failures
If the detached worker cannot be started or the target fails to start:
- keep the job record/index entry
- mark the record failed with a useful issue field
- do not delete the handle the user just received

### 3. Surface immediate job identity
Keep stdout compatible with existing scripts (friendly name), but ensure the handle is emitted before any risky launch work can erase it.

### 4. Update docs and tests
- Update `docs/product.md`, `docs/arch.md`, `README.md`, and `skills/bg-jobs/SKILL.md` to describe the immediate-return behavior.
- Add tests for immediate return and for preserving failed launch records.

## Files to modify
- `src/agentcli_helpers/bg.py`
- `tests/test_bg_redesign.py`
- `docs/product.md`
- `docs/arch.md`
- `README.md`
- `skills/bg-jobs/SKILL.md`

## Verification
- Run a long-sleep smoke test and confirm `bg run` returns immediately.
- Confirm the job record stays present when launch worker startup fails.
- Confirm successful launches still write PID metadata later.

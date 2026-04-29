# Implementation Plan: treat bg jobs as running until proven failed

## Goal
Remove user-visible `launching` behavior. A bg job should be treated as running immediately after handle creation, and only become non-running when there is evidence it failed.

## Behavior target
- `bg run` still returns immediately with the job handle.
- The job record is created as running/in-progress, not launching for user-facing status.
- A detached best-effort probe runs in the background for a short window (about 5s) to capture PID and update the record.
- If PID is never found, the job remains queryable and still appears running until failure is proven.
- Launch or probe failures should preserve the handle and surface a failure record issue rather than deleting the job.

## Changes

### 1. Normalize launch states away from user-facing status
Update status derivation and list rendering so `launching` / `starting` do not show up to users as a terminal or separate state.
- Prefer `running` until exit/failure evidence exists.
- Keep any launch-in-progress detail internal if needed.

### 2. Add a delayed PID probe path
Extend the detached launch flow so the worker records its own PID or a follow-up probe can inspect the worker/process tree for up to ~5s and persist a PID when found.
- Use the existing detached worker as the launch initiator.
- Capture/store the launch-worker PID as best-effort metadata.
- Add a short background probe loop to update `pid` when available.

### 3. Preserve record visibility on launch problems
If spawning the worker fails, mark the record failed with `record_issue` but keep the handle.

### 4. Update docs and tests
- Update `docs/product.md`, `docs/arch.md`, `README.md`, and `skills/bg-jobs/SKILL.md` to describe running-until-proven-failed semantics.
- Add/adjust tests to verify:
  - immediate return still happens
  - no user-visible launching status
  - PID is best-effort updated later
  - failed launch/probe keeps the record

## Files to modify
- `src/agentcli_helpers/bg.py`
- `tests/test_bg_redesign.py`
- `docs/product.md`
- `docs/arch.md`
- `README.md`
- `skills/bg-jobs/SKILL.md`

## Verification
- Run focused bg regression tests.
- Smoke test `bg run` against a long sleep and confirm it returns immediately.
- Check `bg status` and `bg list` no longer present `launching` as the user-facing state.

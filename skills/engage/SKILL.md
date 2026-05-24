---
name: engage
description: >
  Autonomous execution mode triggered by the word "engage". Use when the user has
  finished planning and wants the agent to execute autonomously without further questions
  until the workflow is fully complete. The agent must build, test, verify, and deliver
  proof of work — never exiting with an incomplete or unverified result. Trigger on:
  "engage", "go autonomous", "execute the plan", "run it", "make it happen", or any
  explicit signal to switch from planning mode into fully autonomous build-and-verify mode.
---

# Engage — Autonomous Execution Mode

Switch from planning into autonomous execution. Once engaged, the agent operates without
asking questions, stopping only for catastrophic unrecoverable failures. The goal is a
complete, verified, documented deliverable with proof that it works.

**Golden rule:** If you say you're done, you must have built it, tested it, verified it,
and proven it. No exceptions. No "it should work" — only "it works, and here is the proof."

---

## When to Engage

Engage when:

- The user explicitly says **"engage"**, **"go"**, **"execute"**, **"run it"**, or similar
- A plan has been discussed and the user signals autonomous execution is desired
- The user says "don't ask me again, just do it"
- A prior planning phase is complete and the next step is pure execution

Do **not** engage when:

- The user is still actively debating options or scope
- Critical decisions remain unmade that would change the implementation
- The task is purely research or exploration with no build target

---

## Operating Principle

**Plan → Engage → Execute → Verify → Deliver**

Engage is the bridge between planning and a finished, verified outcome. It is not a
license to skip steps — it is a contract to complete *all* steps without interrupting
the user.

Once engaged:

- **No questions.** Do not use the `question` tool. Do not ask for clarification.
- **No early exits.** Do not mark done until verification is complete.
- **No hand-waving.** "Should work" is not acceptable. Proof is required.
- **Full professionalism.** Update everything the project implies: code, tests, docs,
  config, build artifacts, and verification results.

The only valid reason to stop early is a **catastrophic, unrecoverable failure** that
makes further progress impossible — e.g., the environment cannot install a required
toolchain, a network dependency is permanently unreachable, or a hard external blocker
exists that no workaround can bypass. Even then, report the failure with full context
and what was attempted.

---

## Execution Workflow

### 1. Orient — Understand the Project Landscape

Before touching code, determine what kind of project this is and what "done" looks like.

Assess:

- **Documentation:** Is there documentation? Is it extensive? What format? (`README.md`,
  `docs/`, inline docs, etc.)
- **Tests:** Are tests present? What framework? What coverage exists? Are they passing?
- **Build / Deliverable:** What builds the project? What is the deliverable? (binary,
  package, deployed site, library, etc.)
- **Verification:** How does the project verify itself? (test suites, lint, type check,
  build steps, staging deploy, manual checks)
- **Project conventions:** `AGENTS.md`, `CONTRIBUTING.md`, CI configs, or any established
  workflow files that define expected agent behavior.

Output: A concise internal summary of the project type, build system, test setup, and
verification mechanisms. Use this to inform the execution plan.

### 2. Plan — Confirm or Build the Execution Plan

If a plan already exists from prior discussion, review it against the project landscape.
If none exists, create one.

The plan must include:

- Specific files or components to change
- Tests to add, update, or run
- Documentation to update
- Build steps to execute
- Verification steps to prove correctness
- Definition of done: what artifact proves completion?

Keep the plan visible — write it to a scratchpad or the chat context so the execution
trace is clear.

### 3. Execute — Edit, Build, and Sync

Work through the plan. Use parallel tasks where safe and efficient.

Execution rules:

- **Read the full plan before touching anything.** Do not start editing files until
  you understand the complete scope and order of work.
- **Stay bounded to the plan.** Do not broaden scope, re-architect, or perform
  tangential refactors unless directly required for correctness. Keep work focused
  on the plan and its directly required supporting changes.
- **Self-check as you go.** Catch your own mistakes during execution, not just in
  verification. Read enough surrounding code to integrate cleanly.
- **Do not stop for minor ambiguity.** If the plan leaves ordinary implementation
  details open, make the best bounded call from context and keep moving. Do not
  ask the user about style preferences or minor uncertainties.
- **Code:** Make the required changes. Keep commits logical if the project uses git.
- **Tests:** Update tests to match changes. Add tests for new behavior. Do not break
  existing tests without fixing them.
- **Documentation:** Update all docs affected by the change. `README`, inline comments,
  API docs, changelogs — whatever the project uses.
- **Build:** If the project has a build step, run it. If it produces an artifact
  (binary, package, site), produce it.
- **Configuration:** Update configs, manifests, type definitions, or schemas as needed.
- **Hygiene:** Run linters, formatters, type checkers, or any other project-standard
  quality gates.

**Do not claim completion at this stage.** Execution alone is not done.

### 4. Verify — Prove It Works

This is the critical step that separates engage from a lazy edit-and-pray.

Verification must include at least one of the following, depending on project type:

- **Test execution:** Run the full test suite. Fix failures. Re-run until green.
- **Build verification:** Build the project. Confirm the artifact is produced and valid.
- **Runtime verification:** Run the binary, start the server, open the site, or invoke
  the tool. Confirm it behaves as intended.
- **Deployment / staging check:** If the project deploys, verify the deployment succeeded
  and the change is live and functional.
- **Lint / type / format checks:** Run all quality gates. Fix issues. Re-run until clean.

**Proof of work is mandatory.** The final response must include:

- What was built or changed
- Test results (pass/fail counts, or confirmation of success)
- Build results (artifact produced, no errors)
- Verification results (ran it, checked it, saw it work)
- Any caveats or known limitations

If a verification step fails, fix it and re-verify. Do not return a "mostly done" result.

### 5. Deliver — Clear, Complete, Professional Handoff

The final response must be a clean deliverable summary. The star of the show is the
**actual, checkable result** — the user should be able to immediately verify the work
without additional effort from the agent.

Include:

- **Done:** What was implemented or changed
- **Files:** Key files modified, created, or deleted
- **Validation:** What you ran or checked before returning (tests, builds, runtime checks,
  deployment verification)
- **Proof:** Concrete, immediate evidence the result is real and functional — the exact
  artifact, URL, command output, or observable outcome the user can go check right now
- **Decisions:** Meaningful implementation choices you made when the plan left details open
- **Assumptions:** What you inferred from context when ambiguity existed
- **Deviations:** Anything you changed from the written plan and why
- **Blockers:** Anything unresolved or intentionally deferred, with reason
- **Next steps:** If the user should review, approve, or deploy further, say so

The user should be able to read the summary, see exactly where to look, and confirm
the result is real — not just claimed.

---

## What "Done" Means

A task is only done when:

1. The code changes are complete and correct
2. Tests are updated, added, and passing
3. Documentation reflects the changes
4. The project builds cleanly (if applicable)
5. The change has been run, tested, or deployed and verified to work
6. A clear proof of work is provided
7. The user can inspect the result without additional work from the agent

**Not done:**
- Code edited but tests not run
- Tests passing but build not attempted
- Build succeeding but artifact never executed or checked
- Docs not updated
- "It should work" without verification

---

## Catastrophic Failure — When to Stop

Only stop early if a hard blocker makes further progress impossible despite reasonable
workarounds. Examples:

- Required compiler or toolchain cannot be installed in the environment
- Permanent network failure blocking required dependencies
- Missing permissions that cannot be resolved (and are required)
- External service is down and the task is fundamentally impossible without it
- The plan contains a real contradiction that makes correct execution impossible
- Continuing would clearly cause destructive or unsafe behavior outside the approved scope

In such cases:

1. Document the blocker precisely
2. State what was attempted to resolve it
3. Report what *was* completed before the blocker
4. Mark the task as failed/blocked with full context

Do **not** stop for:

- A test failing (fix it)
- A build error (fix it)
- A linter complaint (fix it)
- Uncertainty about whether something is needed (assume the project conventions require it)
- Wanting to ask the user a preference (decide based on project norms and move forward)

---

## Example Triggers

- User: "Okay, that plan looks good. **Engage.**"
- User: "**Execute the plan.** Don't ask me anything else."
- User: "**Go autonomous.** Build it, test it, and show me it works."
- User: "**Make it happen.** I don't want to babysit."
- User: "**Run it.**" (after a clear plan has been established)

## Example Deliverable

> **Engage complete.**
>
> **Done:** Implemented JWT refresh token rotation in the auth module.
>
> **Files:**
> - `src/auth/login.ts` — refresh logic
> - `tests/auth/login.test.ts` — 4 new tests for rotation
> - `docs/auth.md` — updated flow diagram and endpoint docs
> - `CHANGELOG.md` — v2.4.0 entry
>
> **Validation:**
> - `pnpm test` — 47/47 passing (was 43/43)
> - `pnpm build` — clean, no errors
> - `pnpm lint` — clean
> - Ran dev server, hit `/auth/refresh` with expired token — returned 200 with new token
>
> **Proof:**
> - Build artifact: `dist/app-v2.4.0.zip`
> - Preview URL live and verified: `https://preview-123.app`
> - End-to-end login flow tested and working
>
> **Decisions:** Used 15-minute sliding window for refresh expiration to match existing
> session timeout convention.
>
> **Assumptions:** The `REFRESH_SECRET` env var is already set in staging; if missing,
> the endpoint will 500.
>
> **Deviations:** None — followed the plan exactly.
>
> **Blockers:** None.
>
> **Next steps:** Ready for production deploy when approved.

---

## Summary

Engage is autonomy with accountability. Plan with the user, then execute flawlessly
without interruption. Build it. Test it. Verify it. Prove it. Deliver a result a
professional would be proud to hand over — not a half-finished mess that needs cleanup.

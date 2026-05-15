# Plan: Crony CWD Preservation + `--cron` Flag
_Two gaps: crony doesn't remember the working directory (so relative scripts break when run by the OS scheduler), and there's no way to pass raw cron expressions without the `every ` hack._

---

# Checklist
- [x] Step 1: Add CWD capture + `--cron` flag to `crony.py`
- [x] Step 2: Wrap commands with `cd` in OS scheduler registration (crontab + schtasks)
- [x] Step 3: Clean up Windows `.bat` wrappers on job removal
- [x] Step 4: Update `docs/product.md` with new behavior
- [x] Step 5: Update `docs/arch.md` with new architecture details
- [x] Step 6: Update `skills/crony/SKILL.md`
- [x] Step 7: Verify end to end

---

## Context
- Working directory: `C:\Users\rx\001_Code\100_M\AgentCLI_Helpers`
- Implementation file: `src/agentcli_helpers/crony.py` (651 lines)
- Canon docs: `docs/product.md`, `docs/arch.md`
- Skill file: `skills/crony/SKILL.md`
- All jobs are registered with the OS scheduler (crontab on Unix, schtasks on Windows) via `register_job()`
- Currently, `add_job()` stores `name`, `cmd`, `created_at`, and parsed schedule — but NOT the working directory
- Currently, `register_job_crontab()` and `register_job_task_scheduler()` pass `cmd` verbatim to the OS scheduler with no directory context
- Currently, `parse_schedule()` only supports natural language (must start with `every `/`each ` for recurring, or a date string for one-off)

## Prerequisites
- Work from repo root: `C:\Users\rx\001_Code\100_M\AgentCLI_Helpers`
- Python >= 3.10
- Run `uv sync --extra crony` before verification so the venv has dateparser + croniter
- The venv is at `.venv/` and the prompt will show `(agentcli-helpers)` when active

## Scope Boundaries
- Do not modify `notify.py`, `bg.py`, `screenshot.py`, or `tasks.py`
- Do not modify `pyproject.toml`
- Do not add new CLI commands beyond the `--cron` flag
- Do not change how existing `crony list`, `crony rm`, `crony run`, or `crony logs` behave

---

## Steps

### Step 1: Add CWD capture + `--cron` flag to `crony.py`
Open `src/agentcli_helpers/crony.py`.

**1a. Add `import shlex` at the top of the file** (platform quoting for Unix paths).

**1b. Add `import os` if not already imported** (it's already imported at line 17).

**1c. Modify `add_job()` function (line 244):**

Add an optional `cron_expr: str | None = None` parameter.

When `cron_expr` is provided (bypass natural language parsing):
```python
if cron_expr:
    # Validate: 5 space-separated fields
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        raise ValueError(f"Invalid cron expression: {cron_expr!r}. Expected 5 space-separated fields.")
    parsed = {
        "type": "recurring",
        "interval": cron_expr,
        "cron_expr": cron_expr,
        "next_run": None,
    }
else:
    parsed = parse_schedule(schedule)
```

Add CWD capture. The job dict should include:
```python
job = {
    "name": name,
    "cmd": cmd,
    "created_at": datetime.now().isoformat(),
    "cwd": os.getcwd(),
    **parsed,
}
```

The `cwd` field goes before `**parsed` so parsed keys take precedence over it (though no parsed key named `cwd` exists).

**1d. Modify the `add` CLI command (line 547):**

Add a `--cron` option:
```python
@click.option("--cron", is_flag=True, help="Treat schedule as a raw cron expression (5 fields)")
```

When `cron` is true, call `add_job(name, schedule, cmd, cron_expr=schedule)` and display the result. When false, keep the existing natural-language path.

Display should show the cron expression when `--cron` was used:
```
Added job: myjob
  Schedule: */5 * * * * (recurring, raw cron)
```

✅ Success: `add_job()` accepts `cron_expr`, stores `cwd`, and the `add` CLI has a `--cron` flag.
❌ If failed: Revert changes to `crony.py` and stop. Report what couldn't be added.

### Step 2: Wrap commands with `cd` in OS registration
Open `src/agentcli_helpers/crony.py`.

**2a. Modify `register_job_crontab()` (line 281):**

Read `cwd` from the job dict. If present, wrap the command:
```python
cwd = job.get("cwd", "")
if cwd:
    cmd = f"cd {shlex.quote(cwd)} && {cmd}"
```
Use this wrapped `cmd` in the cron line instead of the raw `cmd`.

**2b. Modify `register_job_task_scheduler()` (line 323):**

Read `cwd` from the job dict. If present, write a `.bat` wrapper script to `~/.crony/scripts/{name}.bat`:
```batch
@echo off
cd /d "CWD"
CMD
```
Then register the `.bat` file path as the task action (`/TR`) instead of the raw command.

For cleaner path handling on Windows:
- Create `~/.crony/scripts/` directory if it doesn't exist (use `Path.mkdir(parents=True, exist_ok=True)`)
- Write the `.bat` file with `pathlib.Path.write_text()`
- Use `str(bat_path)` as the task command

The `.bat` wrapper approach avoids all Windows quoting edge cases: paths with spaces, special characters, nested quotes, etc. Task Scheduler runs `cmd.exe` on the `.bat`, which handles `cd /d "..."` natively.

✅ Success: `register_job_crontab()` wraps command with `cd` via `shlex.quote()`. `register_job_task_scheduler()` writes a `.bat` wrapper when `cwd` is set.
❌ If failed: Revert changes to both registration functions. Stop and report.

### Step 3: Clean up Windows `.bat` wrappers on job removal
Open `src/agentcli_helpers/crony.py`.

**3a. Modify `unregister_job()` (line 407):**

After removing the OS scheduler entry, also clean up the `.bat` wrapper if it exists:
```python
# Clean up Windows batch wrapper
bat_path = CRONY_DIR / "scripts" / f"{name}.bat"
if bat_path.exists():
    bat_path.unlink()
```

✅ Success: `unregister_job()` deletes `~/.crony/scripts/{name}.bat` when present.
❌ If failed: Just note the gap and move on — stale `.bat` files are harmless.

### Step 4: Update `docs/product.md`
Open `docs/product.md`.

**4a. In the `crony add` section (line 251+):**

Add documentation for the `--cron` flag:
```markdown
#### `crony add NAME SCHEDULE "CMD" [--cron]`

...

**Options:**
- `--cron` — Treat SCHEDULE as a raw cron expression instead of natural language

**Examples with `--cron`:**
```bash
crony add myjob "*/5 * * * *" "python script.py" --cron
crony add nightly "0 2 * * *" "backup.sh" --cron
```
```

**4b. In the Edge Cases section (line 343+):**

Add note about CWD preservation:
```markdown
- **Working directory:** When a job is added, crony captures the current working directory.
  When the OS scheduler runs the job, it first `cd`s to that directory, so relative paths work.
  On Unix, this uses `shlex.quote()` for safe path handling. On Windows, a `.bat` wrapper script
  is created in `~/.crony/scripts/` with proper quoting.
```

✅ Success: `docs/product.md` documents `--cron` and CWD preservation.

### Step 5: Update `docs/arch.md`
Open `docs/arch.md`.

**5a. In the CLI interface section (around the `crony add` area, ~line 270):**

Document the `--cron` option.

**5b. In Schedule Parsing section (~line 293):**

Add a note that `--cron` bypasses `parse_schedule()` entirely and creates a recurring job with the raw cron expression.

**5c. In OS Integration section (~line 325):**

Update both the crontab and Windows registration diagrams to show the `cd` wrapping:
- Crontab: `cd {quoted_cwd} && {cmd}`
- Windows: Write `.bat` → register `.bat` path

✅ Success: `docs/arch.md` reflects the new architecture.

### Step 6: Update `skills/crony/SKILL.md`
Open `skills/crony/SKILL.md`.

**6a. Add `--cron` flag to usage and examples.**

**6b. Add note about CWD preservation.**

✅ Success: Skill file documents both features.

### Step 7: Verify end to end
Make sure `.venv` has crony deps:
```bash
uv sync --extra crony
```

**7a. Verify `--cron` flag:**
```bash
uv run crony add test-cron "*/5 * * * *" "python -c \"print('cron')\"" --cron
uv run crony list
uv run crony rm test-cron
```

Expected: `crony add` shows `(recurring, raw cron)`, `crony list` shows the job with a next run time.

**7b. Verify CWD capture:**
```bash
cd /tmp
echo "print('hello')" > test_script.py
uv run --directory C:\Users\rx\001_Code\100_M\AgentCLI_Helpers crony add test-cwd "in 5m" "python test_script.py"
```
Then inspect `~/.crony/jobs.json` to confirm `cwd` field is set to the directory where the job was added.

**7c. Verify `unregister_job()` handles old-format jobs (no `cwd`):**
```bash
uv run crony add test-legacy "in 5m" "python test_script.py"
```
Should not crash even though this job has no `cwd` field.

```bash
uv run crony rm test-legacy
```

✅ Success: `--cron` creates jobs correctly. `cwd` is stored in jobs.json. Jobs without `cwd` still work.
❌ If failed: Save exact command output. Report the failing case.

---

## Verification
- `uv run crony add test-cron "*/5 * * * *" "echo hello" --cron` succeeds and shows raw cron expression in output
- `~/.crony/jobs.json` contains `"cwd": "..."` for new jobs
- `~/.crony/scripts/{name}.bat` is created on Windows when cwd is present
- `uv run crony rm test-cron` removes the job and cleans up its `.bat` file
- Old jobs without `cwd` field don't cause errors

## Rollback
Revert code changes:
```bash
git checkout -- src/agentcli_helpers/crony.py
```
Revert doc changes:
```bash
git checkout -- docs/product.md docs/arch.md skills/crony/SKILL.md
```

Clean up any test artifacts:
```bash
rm -Force ~\.crony\jobs.json -ErrorAction SilentlyContinue
rm -r -Force ~\.crony\scripts -ErrorAction SilentlyContinue
```

Plan complete.

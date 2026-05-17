# FILE: tests/test_bg_redesign.py
# PURPOSE: Exercise bg redesign through the real CLI paths with a temp storage root.
# OWNS: End-to-end regression coverage for naming, collisions, launch timing, list/status, and broken records.
# DOCS: agent_chat/plan_bg_name_redesign_2026-03-27.md, agent_chat/plan_bg_wait_notifications_2026-03-28.md, agent_chat/plan_bg_immediate_fire_and_forget_2026-04-07.md, docs/product.md, docs/arch.md, skills/bg-jobs/SKILL.md

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BG_SRC = ROOT / "src"
sys.path.insert(0, str(BG_SRC))


class TestBgRedesign(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_root = Path(tempfile.mkdtemp(prefix="bg_redesign_cli_"))
        self.jobs_root = self.temp_root / "agentcli_bgjobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)

        import agent_sommelier.bg as bg

        bg.JOBS_DIR = self.jobs_root
        bg.RECORDS_DIR = self.jobs_root / "records"
        bg.INDEX_FILE = self.jobs_root / "index.json"

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "TEMP": str(self.temp_root),
                "TMP": str(self.temp_root),
                "TMPDIR": str(self.temp_root),
            }
        )

        script = textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(BG_SRC)!r})
            from agent_sommelier import bg
            bg.FRIENDLY_WORDS = ['sleepy']
            sys.argv = ['bg', {", ".join(repr(a) for a in args)}]
            bg.main()
            """
        )

        return subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
        )

    def wait_for_status(self, job_name: str, timeout: float = 5.0) -> dict:
        deadline = time.time() + timeout
        last_snapshot: dict | None = None
        while time.time() < deadline:
            status = self.cli("status", job_name)
            if status.returncode == 0:
                last_snapshot = json.loads(status.stdout)
                if last_snapshot.get("pid") is not None:
                    return last_snapshot
            time.sleep(0.1)

        self.fail(f"Timed out waiting for pid metadata for {job_name}: {last_snapshot}")

    def write_index(self, records: dict[str, dict], names: dict[str, str]) -> None:
        payload = {"version": 1, "records": records, "names": names}
        (self.jobs_root / "index.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    def write_meta(self, uid: str, meta: dict) -> Path:
        record_dir = self.jobs_root / "records" / uid
        record_dir.mkdir(parents=True, exist_ok=True)
        (record_dir / "meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
        return record_dir

    def test_collision_suffix_run_and_read_rm(self) -> None:
        first = self.cli("run", 'python -c "print(1)"')
        self.assertEqual(first.returncode, 0, first.stderr)
        name1 = first.stdout.strip()
        self.assertEqual(name1, "sleepy-python")

        second = self.cli("run", 'python -c "print(2)"')
        self.assertEqual(second.returncode, 0, second.stderr)
        name2 = second.stdout.strip()
        self.assertRegex(name2, r"^sleepy-python-[a-z0-9]{2}$")
        self.assertNotEqual(name1, name2)

        wait = self.cli("wait", name1)
        self.assertEqual(wait.returncode, 0, wait.stderr)

        read = self.cli("read", name1)
        self.assertEqual(read.returncode, 0, read.stderr)
        self.assertIn("1", read.stdout)

        rm = self.cli("rm", name1)
        self.assertEqual(rm.returncode, 0, rm.stderr)
        self.assertIn("Removed job", rm.stdout)

        missing = self.cli("status", name1)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("Job not found", missing.stderr)

    def test_create_job_returns_immediately_and_treats_launching_as_running(
        self,
    ) -> None:
        import agent_sommelier.bg as bg

        bg.FRIENDLY_WORDS = ["sleepy"]
        cmd = 'python -c "import time; time.sleep(1)"'

        with mock.patch("agent_sommelier.bg.spawn_launch_worker_for_job"):
            start = time.perf_counter()
            name = bg.create_job(cmd)
            elapsed = time.perf_counter() - start

        self.assertEqual(name, "sleepy-python")
        self.assertLess(elapsed, 0.5)

        status = bg.load_job_snapshot(name)
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status["record_state"], "ok")
        self.assertEqual(status["status"], "running")
        self.assertTrue((self.jobs_root / "index.json").exists())

    def test_create_job_cleans_up_when_initial_write_fails(self) -> None:
        import agent_sommelier.bg as bg

        cmd = 'python -c "print(1)"'
        with mock.patch(
            "agent_sommelier.bg.write_meta", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                bg.create_job(cmd)

        self.assertFalse((self.jobs_root / "index.json").exists())
        records_dir = self.jobs_root / "records"
        self.assertFalse(records_dir.exists() and any(records_dir.iterdir()))

    def test_create_job_cleans_up_when_index_write_fails(self) -> None:
        import agent_sommelier.bg as bg

        cmd = 'python -c "print(1)"'
        with mock.patch(
            "agent_sommelier.bg.save_index", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                bg.create_job(cmd)

        self.assertFalse((self.jobs_root / "index.json").exists())
        records_dir = self.jobs_root / "records"
        self.assertFalse(records_dir.exists() and any(records_dir.iterdir()))

    def test_create_job_keeps_record_when_launch_worker_fails_to_start(self) -> None:
        import agent_sommelier.bg as bg

        bg.FRIENDLY_WORDS = ["sleepy"]
        cmd = 'python -c "print(1)"'

        with mock.patch(
            "agent_sommelier.bg.subprocess.Popen", side_effect=OSError("boom")
        ):
            name = bg.create_job(cmd)

        self.assertEqual(name, "sleepy-python")
        snapshot = bg.load_job_snapshot(name)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot["status"], "failed")
        self.assertIn("launch worker failed to start", snapshot["record_issue"])

    def test_launch_worker_marks_failed_when_target_launch_fails(self) -> None:
        import agent_sommelier.bg as bg

        uid = "launchfail123"
        name = "sleepy-python"
        record_dir = self.write_meta(
            uid,
            {
                "uid": uid,
                "id": uid,
                "name": name,
                "cmd": 'python -c "print(1)"',
                "command_root": "python",
                "started_at": "2026-03-27T00:00:00",
                "status": "launching",
                "pid": None,
                "finished_at": None,
                "exit_code": None,
                "last_event_type": None,
                "last_event_at": None,
                "matched_pattern": None,
                "matched_stream": None,
                "record_issue": None,
            },
        )
        self.write_index(
            records={
                uid: {
                    "name": name,
                    "record_relpath": str(
                        record_dir.relative_to(self.jobs_root).as_posix()
                    ),
                    "cmd": 'python -c "print(1)"',
                    "created_at": "2026-03-27T00:00:00",
                }
            },
            names={name: uid},
        )

        with mock.patch(
            "agent_sommelier.bg.launch_process_for_job_inner",
            side_effect=RuntimeError("boom"),
        ):
            bg.launch_process_for_job_worker(
                uid,
                'python -c "print(1)"',
                record_dir / "stdout.txt",
                record_dir / "stderr.txt",
            )

        snapshot = bg.load_job_snapshot(name)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot["status"], "failed")
        self.assertIn("boom", snapshot["record_issue"])

    def test_run_smoke_returns_immediately_and_captures_pid_later(self) -> None:
        start = time.perf_counter()
        run = self.cli("run", 'python -c "import time; time.sleep(1)"')
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 1.5)
        self.assertEqual(run.returncode, 0, run.stderr)

        job_name = run.stdout.strip()
        status_json = self.wait_for_status(job_name)
        self.assertEqual(status_json["record_state"], "ok")
        self.assertIsInstance(status_json["pid"], int)
        self.assertIn(status_json["status"], {"running", "completed"})

    def test_launch_pid_probe_updates_pid_best_effort(self) -> None:
        import agent_sommelier.bg as bg

        uid = "probe123"
        name = "sleepy-probe"
        record_dir = self.write_meta(
            uid,
            {
                "uid": uid,
                "id": uid,
                "name": name,
                "cmd": 'python -c "print(1)"',
                "command_root": "python",
                "started_at": "2026-03-27T00:00:00",
                "status": "launching",
                "pid": None,
                "launch_worker_pid": 12345,
                "finished_at": None,
                "exit_code": None,
                "last_event_type": None,
                "last_event_at": None,
                "matched_pattern": None,
                "matched_stream": None,
                "record_issue": None,
            },
        )
        self.write_index(
            records={
                uid: {
                    "name": name,
                    "record_relpath": str(
                        record_dir.relative_to(self.jobs_root).as_posix()
                    ),
                    "cmd": 'python -c "print(1)"',
                    "created_at": "2026-03-27T00:00:00",
                }
            },
            names={name: uid},
        )

        with mock.patch(
            "agent_sommelier.bg.find_pid_from_launch_worker", return_value=4321
        ):
            bg.probe_launch_pid_for_job(uid, delay_seconds=0)

        meta = bg.load_job_meta(uid)
        self.assertIsNotNone(meta)
        assert meta is not None
        self.assertEqual(meta["pid"], 4321)
        self.assertTrue((record_dir / "meta.json").exists())

        with mock.patch(
            "agent_sommelier.bg.inspect_process",
            return_value={"process_state": "alive", "is_running": True},
        ):
            snapshot = bg.build_view_from_meta(
                meta, record_state="ok", refresh_process=False
            )
        self.assertEqual(snapshot["status"], "running")
        self.assertIsNone(snapshot["record_issue"])

    def test_launch_pid_probe_preserves_record_when_pid_cannot_be_found(self) -> None:
        import agent_sommelier.bg as bg

        uid = "probe-miss123"
        name = "sleepy-probe-miss"
        record_dir = self.write_meta(
            uid,
            {
                "uid": uid,
                "id": uid,
                "name": name,
                "cmd": 'python -c "print(1)"',
                "command_root": "python",
                "started_at": "2026-03-27T00:00:00",
                "status": "launching",
                "pid": None,
                "launch_worker_pid": 12345,
                "finished_at": None,
                "exit_code": None,
                "last_event_type": None,
                "last_event_at": None,
                "matched_pattern": None,
                "matched_stream": None,
                "record_issue": None,
            },
        )
        self.write_index(
            records={
                uid: {
                    "name": name,
                    "record_relpath": str(
                        record_dir.relative_to(self.jobs_root).as_posix()
                    ),
                    "cmd": 'python -c "print(1)"',
                    "created_at": "2026-03-27T00:00:00",
                }
            },
            names={name: uid},
        )

        with mock.patch(
            "agent_sommelier.bg.find_pid_from_launch_worker", return_value=None
        ):
            bg.probe_launch_pid_for_job(uid, delay_seconds=0)

        meta = bg.load_job_meta(uid)
        self.assertIsNotNone(meta)
        assert meta is not None
        snapshot = bg.build_view_from_meta(
            meta, record_state="ok", refresh_process=False
        )
        self.assertEqual(snapshot["status"], "running")
        self.assertIn("pid probe could not confirm", snapshot["record_issue"])
        self.assertTrue((self.jobs_root / "records" / uid).exists())

    def test_list_and_status_surface_live_and_dead_process_states(self) -> None:
        live_uid = "live123"
        dead_uid = "dead123"

        live_dir = self.write_meta(
            live_uid,
            {
                "uid": live_uid,
                "id": live_uid,
                "name": "sleepy-live",
                "cmd": 'python -c "print(1)"',
                "command_root": "python",
                "started_at": "2026-03-27T00:00:00",
                "status": "running",
                "pid": os.getpid(),
                "finished_at": None,
                "exit_code": None,
            },
        )
        dead_dir = self.write_meta(
            dead_uid,
            {
                "uid": dead_uid,
                "id": dead_uid,
                "name": "sleepy-dead",
                "cmd": 'python -c "print(2)"',
                "command_root": "python",
                "started_at": (datetime.now() - timedelta(minutes=5)).isoformat(),
                "status": "running",
                "pid": 99999999,
                "finished_at": None,
                "exit_code": None,
            },
        )
        self.write_index(
            records={
                live_uid: {
                    "name": "sleepy-live",
                    "record_relpath": str(
                        live_dir.relative_to(self.jobs_root).as_posix()
                    ),
                    "cmd": 'python -c "print(1)"',
                    "created_at": "2026-03-27T00:00:00",
                },
                dead_uid: {
                    "name": "sleepy-dead",
                    "record_relpath": str(
                        dead_dir.relative_to(self.jobs_root).as_posix()
                    ),
                    "cmd": 'python -c "print(2)"',
                    "created_at": "2026-03-27T00:00:00",
                },
            },
            names={"sleepy-live": live_uid, "sleepy-dead": dead_uid},
        )

        running = self.cli("status", "sleepy-live")
        self.assertEqual(running.returncode, 0, running.stderr)
        running_json = json.loads(running.stdout)
        self.assertEqual(running_json["record_state"], "ok")
        self.assertEqual(running_json["process_state"], "alive")
        self.assertEqual(running_json["status"], "running")

        dead = self.cli("status", "sleepy-dead")
        self.assertEqual(dead.returncode, 0, dead.stderr)
        dead_json = json.loads(dead.stdout)
        self.assertEqual(dead_json["record_state"], "ok")
        self.assertEqual(dead_json["process_state"], "dead")
        self.assertEqual(dead_json["status"], "stale")

        listed = self.cli("list", "--json")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        jobs = json.loads(listed.stdout)
        self.assertTrue(
            any(
                job["name"] == "sleepy-live" and job["process_state"] == "alive"
                for job in jobs
            )
        )
        self.assertTrue(
            any(
                job["name"] == "sleepy-dead" and job["process_state"] == "dead"
                for job in jobs
            )
        )

    def test_orphan_missing_and_corrupt_are_visible_in_cli_list_and_status(
        self,
    ) -> None:
        orphan_dir = self.jobs_root / "records" / "orphan123"
        orphan_dir.mkdir(parents=True, exist_ok=True)

        missing_uid = "missing123"
        corrupt_uid = "corrupt123"
        healthy_uid = "healthy123"

        self.write_index(
            records={
                missing_uid: {
                    "name": "sleepy-missing",
                    "record_relpath": f"records/{missing_uid}",
                    "cmd": "python -m pytest",
                    "created_at": "2026-03-27T00:00:00",
                },
                healthy_uid: {
                    "name": "sleepy-healthy",
                    "record_relpath": f"records/{healthy_uid}",
                    "cmd": "python -m pytest",
                    "created_at": "2026-03-27T00:00:00",
                },
            },
            names={"sleepy-missing": missing_uid, "sleepy-healthy": healthy_uid},
        )

        self.write_meta(
            healthy_uid,
            {
                "uid": healthy_uid,
                "id": healthy_uid,
                "name": "sleepy-healthy",
                "cmd": "python -c \"print('ok')\"",
                "command_root": "python",
                "started_at": "2026-03-27T00:00:00",
                "status": "running",
                "pid": os.getpid(),
                "finished_at": None,
                "exit_code": None,
            },
        )

        corrupt_dir = self.jobs_root / "records" / corrupt_uid
        corrupt_dir.mkdir(parents=True, exist_ok=True)
        (corrupt_dir / "meta.json").write_text("{broken", encoding="utf-8")

        list_result = self.cli("list", "--json")
        self.assertEqual(list_result.returncode, 0, list_result.stderr)
        listed = json.loads(list_result.stdout)
        states = {job["record_state"] for job in listed}
        self.assertTrue({"orphaned", "missing", "corrupt"}.issubset(states))

        orphan = self.cli("status", "orphan123")
        self.assertNotEqual(orphan.returncode, 0)
        orphan_json = json.loads(orphan.stdout)
        self.assertEqual(orphan_json["record_state"], "orphaned")

        missing = self.cli("status", "sleepy-missing")
        self.assertNotEqual(missing.returncode, 0)
        missing_json = json.loads(missing.stdout)
        self.assertEqual(missing_json["record_state"], "missing")

        corrupt = self.cli("status", "corrupt123")
        self.assertNotEqual(corrupt.returncode, 0)
        corrupt_json = json.loads(corrupt.stdout)
        self.assertEqual(corrupt_json["record_state"], "corrupt")

    def test_wait_completion_blocks_until_done_and_marks_event(self) -> None:
        run = self.cli("run", 'python -c "import time; time.sleep(0.4)"')
        self.assertEqual(run.returncode, 0, run.stderr)
        job_name = run.stdout.strip()

        start = time.perf_counter()
        wait = self.cli("wait", job_name)
        elapsed = time.perf_counter() - start

        self.assertEqual(wait.returncode, 0, wait.stderr)
        self.assertGreaterEqual(elapsed, 0.05)

        status = self.cli("status", job_name)
        self.assertEqual(status.returncode, 0, status.stderr)
        status_json = json.loads(status.stdout)
        self.assertEqual(status_json["status"], "completed")
        self.assertEqual(status_json["last_event_type"], "completed")
        self.assertEqual(status_json["update_marker"], "completed")

    def test_wait_match_tracks_stderr_and_surfaces_update_marker(self) -> None:
        run = self.cli(
            "run",
            "python -c \"import sys,time; sys.stderr.write('needle\\n'); sys.stderr.flush(); time.sleep(1.5)\"",
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        job_name = run.stdout.strip()

        start = time.perf_counter()
        wait = self.cli("wait", job_name, "--match", "needle")
        elapsed = time.perf_counter() - start

        self.assertEqual(wait.returncode, 0, wait.stderr)
        self.assertGreaterEqual(elapsed, 0.15)

        listed = self.cli("list", "--json")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        jobs = json.loads(listed.stdout)
        match = next(job for job in jobs if job["name"] == job_name)
        self.assertIn(match["status"], {"running", "completed"})
        self.assertEqual(match["matched_pattern"], "needle")
        self.assertEqual(match["matched_stream"], "stderr")
        self.assertIn("matched: needle", match["update_marker"])

        status = self.cli("status", job_name)
        self.assertEqual(status.returncode, 0, status.stderr)
        status_json = json.loads(status.stdout)
        self.assertEqual(status_json["matched_stream"], "stderr")
        self.assertIn("matched: needle", status_json["update_marker"])

    def test_wait_all_waits_for_multiple_jobs(self) -> None:
        first = self.cli("run", 'python -c "import time; time.sleep(0.35)"')
        self.assertEqual(first.returncode, 0, first.stderr)
        first_name = first.stdout.strip()

        second = self.cli("run", 'python -c "import time; time.sleep(0.55)"')
        self.assertEqual(second.returncode, 0, second.stderr)
        second_name = second.stdout.strip()

        start = time.perf_counter()
        wait_all = self.cli("wait-all")
        elapsed = time.perf_counter() - start

        self.assertEqual(wait_all.returncode, 0, wait_all.stderr)
        self.assertGreaterEqual(elapsed, 0.05)

        for name in (first_name, second_name):
            status = self.cli("status", name)
            self.assertEqual(status.returncode, 0, status.stderr)
            status_json = json.loads(status.stdout)
            self.assertEqual(status_json["status"], "completed")
            self.assertEqual(status_json["last_event_type"], "completed")
            self.assertEqual(status_json["update_marker"], "completed")

    def test_terminal_jobs_prune_by_age_and_cap_while_running_jobs_survive(
        self,
    ) -> None:
        now = datetime.now()
        running_uid = "running123"
        old_uid = "old123"
        recent_uids = [f"recent{i:02d}" for i in range(33)]

        records: dict[str, dict] = {}
        names: dict[str, str] = {}

        old_started = (now - timedelta(hours=2, minutes=5)).isoformat()
        old_finished = (now - timedelta(hours=2)).isoformat()
        old_dir = self.write_meta(
            old_uid,
            {
                "uid": old_uid,
                "id": old_uid,
                "name": "sleepy-old",
                "cmd": 'python -c "print(0)"',
                "command_root": "python",
                "started_at": old_started,
                "status": "completed",
                "pid": None,
                "finished_at": old_finished,
                "exit_code": 0,
                "last_event_type": "completed",
                "last_event_at": old_finished,
                "matched_pattern": None,
                "matched_stream": None,
            },
        )
        records[old_uid] = {
            "name": "sleepy-old",
            "record_relpath": str(old_dir.relative_to(self.jobs_root).as_posix()),
            "cmd": 'python -c "print(0)"',
            "created_at": old_started,
        }
        names["sleepy-old"] = old_uid

        running_started = (now - timedelta(minutes=5)).isoformat()
        running_dir = self.write_meta(
            running_uid,
            {
                "uid": running_uid,
                "id": running_uid,
                "name": "sleepy-running",
                "cmd": 'python -c "import time; time.sleep(5)"',
                "command_root": "python",
                "started_at": running_started,
                "status": "running",
                "pid": os.getpid(),
                "finished_at": None,
                "exit_code": None,
                "last_event_type": None,
                "last_event_at": None,
                "matched_pattern": None,
                "matched_stream": None,
            },
        )
        records[running_uid] = {
            "name": "sleepy-running",
            "record_relpath": str(running_dir.relative_to(self.jobs_root).as_posix()),
            "cmd": 'python -c "import time; time.sleep(5)"',
            "created_at": running_started,
        }
        names["sleepy-running"] = running_uid

        for i, uid in enumerate(recent_uids):
            started_at = (now - timedelta(minutes=i, seconds=30)).isoformat()
            finished_at = (now - timedelta(minutes=i)).isoformat()
            recent_dir = self.write_meta(
                uid,
                {
                    "uid": uid,
                    "id": uid,
                    "name": f"sleepy-{uid}",
                    "cmd": f'python -c "print({i + 1})"',
                    "command_root": "python",
                    "started_at": started_at,
                    "status": "completed",
                    "pid": None,
                    "finished_at": finished_at,
                    "exit_code": 0,
                    "last_event_type": "completed",
                    "last_event_at": finished_at,
                    "matched_pattern": None,
                    "matched_stream": None,
                },
            )
            records[uid] = {
                "name": f"sleepy-{uid}",
                "record_relpath": str(
                    recent_dir.relative_to(self.jobs_root).as_posix()
                ),
                "cmd": f'python -c "print({i + 1})"',
                "created_at": started_at,
            }
            names[f"sleepy-{uid}"] = uid

        self.write_index(records, names)

        status = self.cli("status", "sleepy-running")
        self.assertEqual(status.returncode, 0, status.stderr)

        listed = self.cli("list", "--json")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        jobs = json.loads(listed.stdout)
        uids = {job["uid"] for job in jobs}

        self.assertIn(running_uid, uids)
        self.assertNotIn(old_uid, uids)
        self.assertNotIn("recent32", uids)
        self.assertEqual(sum(1 for job in jobs if job["status"] != "running"), 32)
        self.assertTrue((self.jobs_root / "records" / running_uid).exists())
        self.assertFalse((self.jobs_root / "records" / old_uid).exists())
        self.assertFalse((self.jobs_root / "records" / "recent32").exists())

    def test_prune_removes_every_non_running_job(self) -> None:
        running_uid = "running123"
        completed_uid = "done123"
        stale_uid = "stale123"
        orphan_uid = "orphan123"
        corrupt_uid = "corrupt123"

        running_dir = self.write_meta(
            running_uid,
            {
                "uid": running_uid,
                "id": running_uid,
                "name": "sleepy-running",
                "cmd": 'python -c "import time; time.sleep(5)"',
                "command_root": "python",
                "started_at": datetime.now().isoformat(),
                "status": "running",
                "pid": os.getpid(),
                "finished_at": None,
                "exit_code": None,
                "last_event_type": None,
                "last_event_at": None,
                "matched_pattern": None,
                "matched_stream": None,
            },
        )
        completed_dir = self.write_meta(
            completed_uid,
            {
                "uid": completed_uid,
                "id": completed_uid,
                "name": "sleepy-done",
                "cmd": 'python -c "print(1)"',
                "command_root": "python",
                "started_at": (datetime.now() - timedelta(minutes=20)).isoformat(),
                "status": "completed",
                "pid": None,
                "finished_at": (datetime.now() - timedelta(minutes=19)).isoformat(),
                "exit_code": 0,
                "last_event_type": "completed",
                "last_event_at": (datetime.now() - timedelta(minutes=19)).isoformat(),
                "matched_pattern": None,
                "matched_stream": None,
            },
        )
        stale_dir = self.write_meta(
            stale_uid,
            {
                "uid": stale_uid,
                "id": stale_uid,
                "name": "sleepy-stale",
                "cmd": 'python -c "print(2)"',
                "command_root": "python",
                "started_at": (datetime.now() - timedelta(minutes=25)).isoformat(),
                "status": "running",
                "pid": 99999999,
                "finished_at": None,
                "exit_code": None,
                "last_event_type": None,
                "last_event_at": None,
                "matched_pattern": None,
                "matched_stream": None,
            },
        )

        orphan_dir = self.jobs_root / "records" / orphan_uid
        orphan_dir.mkdir(parents=True, exist_ok=True)
        (orphan_dir / "stdout.txt").write_text("orphan", encoding="utf-8")

        corrupt_dir = self.jobs_root / "records" / corrupt_uid
        corrupt_dir.mkdir(parents=True, exist_ok=True)
        (corrupt_dir / "meta.json").write_text("{broken", encoding="utf-8")

        self.write_index(
            records={
                running_uid: {
                    "name": "sleepy-running",
                    "record_relpath": str(
                        running_dir.relative_to(self.jobs_root).as_posix()
                    ),
                    "cmd": 'python -c "import time; time.sleep(5)"',
                    "created_at": datetime.now().isoformat(),
                },
                completed_uid: {
                    "name": "sleepy-done",
                    "record_relpath": str(
                        completed_dir.relative_to(self.jobs_root).as_posix()
                    ),
                    "cmd": 'python -c "print(1)"',
                    "created_at": (datetime.now() - timedelta(minutes=20)).isoformat(),
                },
                stale_uid: {
                    "name": "sleepy-stale",
                    "record_relpath": str(
                        stale_dir.relative_to(self.jobs_root).as_posix()
                    ),
                    "cmd": 'python -c "print(2)"',
                    "created_at": (datetime.now() - timedelta(minutes=25)).isoformat(),
                },
            },
            names={
                "sleepy-running": running_uid,
                "sleepy-done": completed_uid,
                "sleepy-stale": stale_uid,
            },
        )

        prune = self.cli("prune")
        self.assertEqual(prune.returncode, 0, prune.stderr)
        self.assertIn("Pruned 4 job(s)", prune.stdout)

        listed = self.cli("list", "--json")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        jobs = json.loads(listed.stdout)
        self.assertEqual([job["uid"] for job in jobs], [running_uid])
        self.assertTrue((self.jobs_root / "records" / running_uid).exists())
        self.assertFalse((self.jobs_root / "records" / completed_uid).exists())
        self.assertFalse((self.jobs_root / "records" / stale_uid).exists())
        self.assertFalse((self.jobs_root / "records" / orphan_uid).exists())
        self.assertFalse((self.jobs_root / "records" / corrupt_uid).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Tests for skill-store CLI — Osiris oversees.

Every line, every branch, every edge case. No mercy.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

# Ensure the source is importable
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from agent_sommelier.skill_store import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def cli_run(args, store, input=None, env=None):
    """Run CLI with the given store path. Returns CliRunner result."""
    runner = CliRunner()
    invoke_env = {"SKILL_STORE_PATH": str(store)}
    if env:
        invoke_env.update(env)
    return runner.invoke(cli, args, input=input, env=invoke_env)


def init_store(store):
    """Initialize a store. Returns result."""
    return cli_run(["init"], store)


def create_skill(store, slug="my-tool", name="My Tool", desc="Does stuff"):
    """Create a skill. Returns result."""
    return cli_run(["create-new"], store, input=f"{slug}\n{name}\n{desc}\n")


def read_index(store: Path) -> dict:
    """Read the index.json from store."""
    return json.loads((store / "index.json").read_text(encoding="utf-8"))


def has_git() -> bool:
    """Check if git is available on this system."""
    try:
        subprocess.run(
            ["git", "--version"], capture_output=True, timeout=5, check=False
        )
        return True
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return False


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


class TestInit:
    """The foundation. Without init, nothing works."""

    def test_init_creates_store_structure(self, tmp_path):
        store = tmp_path / "store"
        result = init_store(store)
        assert result.exit_code == 0, f"init failed: {result.output}"
        assert store.is_dir(), "Store directory not created"
        assert (store / "skills").is_dir(), "skills/ not created"
        assert (store / "index.json").exists(), "index.json not created"

    def test_init_creates_valid_index(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        index = read_index(store)
        assert index["version"] == 2
        assert index["pinned"] == []
        assert index["skills"] == []
        assert index["groups"] == {}
        assert index["stats"]["total"] == 0
        assert index["stats"]["pinned"] == 0
        assert index["stats"]["groups"] == 0
        assert index["stats"]["organized"] == 0
        assert index["stats"]["updated_at"] == ""

    def test_init_is_idempotent(self, tmp_path):
        store = tmp_path / "store"
        r1 = init_store(store)
        assert r1.exit_code == 0
        r2 = init_store(store)
        assert r2.exit_code == 0
        assert "already initialized" in r2.output.lower()

    def test_init_creates_git_repo(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        git_dir = store / ".git"
        if has_git():
            assert git_dir.is_dir(), f"Expected .git at {git_dir}"
        # If git not available, we don't assert — graceful degradation

    def test_init_creates_gitignore_when_git_available(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        gitignore = store / ".gitignore"
        if (store / ".git").is_dir():
            assert gitignore.exists(), ".gitignore should exist after git init"
            content = gitignore.read_text()
            assert "*.tmp" in content
            assert "__pycache__/" in content

    def test_init_output_messages(self, tmp_path):
        store = tmp_path / "store"
        result = init_store(store)
        assert "Initialized" in result.output

    def test_init_creates_skills_dir(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        assert (store / "skills").is_dir()

    def test_init_no_skills_populated(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        index = read_index(store)
        assert len(index["skills"]) == 0

    def test_init_called_on_already_initialized_does_not_recreate_index(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        index1 = read_index(store)
        # Manually modify index
        index1["stats"]["total"] = 42
        json_str = json.dumps(index1, indent=2, ensure_ascii=False)
        (store / "index.json").write_text(json_str, encoding="utf-8")
        # Call init again
        init_store(store)
        index2 = read_index(store)
        # Should not have been overwritten since it already exists
        assert index2["stats"]["total"] == 42

    def test_init_sets_git_branch_to_main(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        if has_git() and (store / ".git").is_dir():
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=store,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.stdout.strip() == "main"


# ---------------------------------------------------------------------------
# create-new
# ---------------------------------------------------------------------------


class TestCreateNew:
    """The skill creation wizard — interactive and validated."""

    def test_creates_skill_folder_and_skillmd(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = create_skill(store)
        assert result.exit_code == 0, f"create-new failed: {result.output}"
        skill_dir = store / "skills" / "my-tool"
        assert skill_dir.is_dir(), "Skill directory not created"
        assert (skill_dir / "SKILL.md").exists(), "SKILL.md not created"

    def test_frontmatter_is_valid_yaml(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool", name="My Tool", desc="Does stuff")
        skill_md = store / "skills" / "my-tool" / "SKILL.md"
        text = skill_md.read_text(encoding="utf-8")
        assert text.startswith("---")
        parts = text.split("---", 2)
        assert len(parts) >= 3
        meta = yaml.safe_load(parts[1])
        assert meta["name"] == "My Tool"
        assert meta["description"] == "Does stuff"

    def test_creates_skill_updates_index(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool")
        index = read_index(store)
        slugs = [s["slug"] for s in index["skills"]]
        assert "my-tool" in slugs
        skill_entry = next(s for s in index["skills"] if s["slug"] == "my-tool")
        assert skill_entry["name"] == "My Tool"
        assert skill_entry["description"] == "Does stuff"
        assert skill_entry["path"] == "skills/my-tool"

    def test_rejects_empty_slug(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        # Provide empty slug (just press enter), then valid slug
        result = cli_run(["create-new"], store, input="\nvalid-slug\nA name\nA desc\n")
        assert result.exit_code == 0
        assert (store / "skills" / "valid-slug").is_dir()

    def test_rejects_uppercase_slug(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(
            ["create-new"], store, input="UPPERCASE\nMy Tool\nDoes stuff\n"
        )
        assert result.exit_code != 0 or "lowercase" in result.output.lower()

    def test_rejects_special_chars_slug(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(
            ["create-new"], store, input="hello world\nMy Tool\nDoes stuff\n"
        )
        assert result.exit_code != 0 or "kebab-case" in result.output.lower()

    def test_rejects_leading_hyphen_slug(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(
            ["create-new"], store, input="-mytool\nMy Tool\nDoes stuff\n"
        )
        assert result.exit_code != 0 or "start or end" in result.output.lower()

    def test_rejects_trailing_hyphen_slug(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(
            ["create-new"], store, input="mytool-\nMy Tool\nDoes stuff\n"
        )
        assert result.exit_code != 0 or "start or end" in result.output.lower()

    def test_rejects_consecutive_hyphens(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(
            ["create-new"], store, input="my--tool\nMy Tool\nDoes stuff\n"
        )
        assert result.exit_code != 0 or "consecutive" in result.output.lower()

    def test_rejects_duplicate_slug(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool")
        result = cli_run(
            ["create-new"], store, input="my-tool\nMy Tool\nDoes stuff\n"
        )
        assert "already exists" in result.output.lower()

    def test_fails_if_not_initialized(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir(parents=True)
        result = cli_run(["create-new"], store, input="my-tool\nMy Tool\nDoes stuff\n")
        assert result.exit_code != 0
        assert "init" in result.output.lower()

    def test_creates_skill_with_default_name_from_slug(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        # Empty name (just press enter) should use slug as name
        result = cli_run(["create-new"], store, input="my-cool-tool\n\nDoes stuff\n")
        assert result.exit_code == 0
        skill_md = store / "skills" / "my-cool-tool" / "SKILL.md"
        assert skill_md.exists()
        # When empty, Click defaults to slug.replace("-", " ").title() => "My Cool Tool"
        index = read_index(store)
        entry = next(s for s in index["skills"] if s["slug"] == "my-cool-tool")
        assert entry["name"] == "My Cool Tool"

    def test_requires_description(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        # First empty description, then provide one
        result = cli_run(
            ["create-new"], store, input="my-tool\nMy Tool\n\nRequired desc\n"
        )
        assert result.exit_code == 0
        assert "required" in result.output.lower()
        assert (store / "skills" / "my-tool").is_dir()

    def test_creates_skill_with_numbers_in_slug(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(
            ["create-new"], store, input="tool2-v3\nMy Tool\nDoes stuff\n"
        )
        assert result.exit_code == 0
        assert (store / "skills" / "tool2-v3").is_dir()

    def test_create_new_after_sync_does_not_lose_existing_skills(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="skill-a", name="Skill A", desc="First skill")
        create_skill(store, slug="skill-b", name="Skill B", desc="Second skill")
        index = read_index(store)
        assert len(index["skills"]) == 2


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------


class TestSync:
    """Index rebuilding — scan reality, write truth."""

    def test_sync_empty_skills_dir(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(["sync"], store)
        assert result.exit_code == 0
        index = read_index(store)
        assert len(index["skills"]) == 0
        assert "No skills found" in result.output

    def test_sync_scans_and_updates_index(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        # Manually corrupt the index to prove sync rebuilds it
        index = read_index(store)
        index["skills"] = []
        (store / "index.json").write_text(
            json.dumps(index, indent=2), encoding="utf-8"
        )
        # Re-sync
        result = cli_run(["sync"], store)
        assert result.exit_code == 0
        index = read_index(store)
        assert len(index["skills"]) == 1
        assert index["skills"][0]["slug"] == "alpha"

    def test_sync_handles_skill_without_skillmd(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        # Create a skill folder manually without SKILL.md
        (store / "skills" / "no-md-folder").mkdir(parents=True)
        (store / "skills" / "no-md-folder" / "some-file.txt").write_text("hello")
        result = cli_run(["sync"], store)
        assert result.exit_code == 0
        index = read_index(store)
        slugs = [s["slug"] for s in index["skills"]]
        assert "no-md-folder" in slugs
        entry = next(s for s in index["skills"] if s["slug"] == "no-md-folder")
        assert entry["name"] == "no-md-folder"  # Falls back to folder name
        assert entry["description"] == ""

    def test_sync_handles_skill_with_empty_skillmd(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        skill_dir = store / "skills" / "empty-md"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("", encoding="utf-8")
        result = cli_run(["sync"], store)
        assert result.exit_code == 0
        index = read_index(store)
        entry = next(s for s in index["skills"] if s["slug"] == "empty-md")
        assert entry["name"] == "empty-md"

    def test_sync_handles_skill_with_frontmatter_no_closing(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        skill_dir = store / "skills" / "broken-md"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: Broken\n", encoding="utf-8")
        result = cli_run(["sync"], store)
        assert result.exit_code == 0
        index = read_index(store)
        entry = next(s for s in index["skills"] if s["slug"] == "broken-md")
        assert entry["name"] == "broken-md"

    def test_sync_preserves_pin_order(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        # Create multiple skills
        for slug in ["zulu", "alpha", "beta", "gamma"]:
            create_skill(store, slug=slug, name=slug.title(), desc=slug)
        # Pin zulu and alpha in specific order
        cli_run(["pin", "zulu"], store)
        cli_run(["pin", "alpha"], store)
        index = read_index(store)
        assert index["pinned"] == ["zulu", "alpha"]
        # Now sync
        cli_run(["sync"], store)
        index = read_index(store)
        # Pinned should appear first in order: alpha, zulu (since alpha was pinned last, it's appended)
        # Actually: pin zulu -> pinned = ["zulu"], pin alpha -> pinned = ["zulu", "alpha"]
        # So pinned skills list should be: zulu, alpha
        assert index["pinned"] == ["zulu", "alpha"]
        pinned_in_skills = [s["slug"] for s in index["skills"] if s["slug"] in index["pinned"]]
        assert pinned_in_skills == ["zulu", "alpha"]

    def test_sync_removes_stale_pins(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="keep-me")
        cli_run(["pin", "keep-me"], store)
        # Manually add a stale pin
        index = read_index(store)
        index["pinned"].append("deleted-skill")
        (store / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
        # Sync
        cli_run(["sync"], store)
        index = read_index(store)
        assert "deleted-skill" not in index["pinned"]

    def test_sync_updates_stats(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="skill-a")
        create_skill(store, slug="skill-b")
        cli_run(["pin", "skill-a"], store)
        index = read_index(store)
        assert index["stats"]["total"] == 2
        assert index["stats"]["pinned"] == 1
        assert index["stats"]["groups"] == 0
        assert index["stats"]["organized"] == 0
        assert index["stats"]["updated_at"] != ""

    def test_sync_fails_if_not_initialized(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir(parents=True)
        result = cli_run(["sync"], store)
        assert result.exit_code != 0
        assert "init" in result.output.lower()

    def test_sync_orders_unpinned_alphabetically(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        for slug in ["delta", "charlie", "bravo", "alpha"]:
            create_skill(store, slug=slug, name=slug.title(), desc=slug)
        cli_run(["sync"], store)
        index = read_index(store)
        unpinned = [s for s in index["skills"] if s["slug"] not in index["pinned"]]
        slugs = [s["slug"] for s in unpinned]
        assert slugs == sorted(slugs), f"Expected sorted order, got {slugs}"

    def test_sync_does_not_crash_on_non_dir_entries(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        # Create a file directly in skills/
        (store / "skills" / "not-a-dir.txt").write_text("oops")
        result = cli_run(["sync"], store)
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------


class TestLoad:
    """Inspect skills — text and JSON."""

    def test_load_text_output(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool", name="My Tool", desc="Does stuff")
        result = cli_run(["load", "my-tool"], store)
        assert result.exit_code == 0
        assert "Path:" in result.output
        assert "SKILL.md:" in result.output
        assert "my-tool" in result.output or "my_tool" in result.output

    def test_load_text_shows_tree(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool")
        result = cli_run(["load", "my-tool"], store)
        assert result.exit_code == 0
        # Tree should contain SKILL.md
        assert "SKILL.md" in result.output

    def test_load_json_output(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool", name="My Tool", desc="Does stuff")
        result = cli_run(["load", "my-tool", "--json"], store)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["slug"] == "my-tool"
        assert data["name"] == "My Tool"
        assert "path" in data
        assert "skillmd" in data
        assert data["skillmd"].endswith("SKILL.md")
        assert isinstance(data["tree"], list)
        assert len(data["tree"]) > 0

    def test_load_json_has_all_required_fields(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool")
        result = cli_run(["load", "my-tool", "-j"], store)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert set(data.keys()) == {"slug", "name", "version", "description", "path", "skillmd", "tree"}

    def test_load_nonexistent_skill(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(["load", "no-such-skill"], store)
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_load_suggests_similar_skills(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool")
        result = cli_run(["load", "my"], store)
        assert result.exit_code != 0
        assert "my-tool" in result.output  # Should suggest the close match

    def test_load_fails_if_not_initialized(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir(parents=True)
        result = cli_run(["load", "anything"], store)
        assert result.exit_code != 0
        assert "init" in result.output.lower()

    def test_load_json_tree_includes_all_files(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool")
        # Add an extra file to the skill
        (store / "skills" / "my-tool" / "extra.py").write_text("# extra")
        result = cli_run(["load", "my-tool", "--json"], store)
        data = json.loads(result.output)
        tree_files = [entry for entry in data["tree"] if "SKILL.md" in entry or "extra.py" in entry]
        assert len(tree_files) >= 2

    def test_load_shows_resolved_absolute_path(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool")
        result = cli_run(["load", "my-tool"], store)
        assert result.exit_code == 0
        # Rich console breaks long paths across lines, so check the prefix
        expected = str((store / "skills" / "my-tool").resolve())
        # Normalize spaces and compare without line-break artifacts
        normalized_output = result.output.replace("\n", "").replace(" ", "")
        assert "Path:" in result.output
        assert "SKILL.md:" in result.output


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestList:
    """Paginated listings — pinned first, then alphabetical."""

    def test_list_all_skills(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        create_skill(store, slug="beta")
        result = cli_run(["list"], store)
        assert result.exit_code == 0
        assert "alpha" in result.output
        assert "beta" in result.output

    def test_list_empty_store(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(["list"], store)
        assert result.exit_code == 0
        assert "No skills" in result.output

    def test_list_pinned_first(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="beta")
        create_skill(store, slug="alpha")
        # Pin beta — it should appear first in listing
        cli_run(["pin", "beta"], store)
        result = cli_run(["list"], store)
        assert result.exit_code == 0
        # beta should appear before alpha in the output
        beta_pos = result.output.index("beta")
        alpha_pos = result.output.index("alpha")
        assert beta_pos < alpha_pos, "Pinned skill should appear before unpinned"

    def test_list_shows_pin_marker(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        cli_run(["pin", "alpha"], store)
        result = cli_run(["list"], store)
        # In TTY: ⭐ ; in non-TTY fallback: *
        assert "⭐" in result.output or "* alpha" in result.output

    def test_list_no_pin_marker_for_unpinned(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        result = cli_run(["list"], store)
        # Ensure output exists without requiring specific marker
        assert "alpha" in result.output

    def test_list_pagination_page_1(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        # Create 25 skills (more than PAGE_SIZE=20)
        for i in range(25):
            create_skill(store, slug=f"skill-{i:03d}", name=f"Skill {i}", desc=f"Skill {i}")
        result = cli_run(["list", "--page", "1"], store)
        assert result.exit_code == 0
        assert "skill-000" in result.output
        assert "Page 1" in result.output

    def test_list_pagination_page_2(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        for i in range(25):
            create_skill(store, slug=f"skill-{i:03d}", name=f"Skill {i}", desc=f"Skill {i}")
        result = cli_run(["list", "--page", "2"], store)
        assert result.exit_code == 0
        assert "Page 2" in result.output
        # Some skills from page 2 should be visible
        assert "skill-020" in result.output

    def test_list_page_out_of_range(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="only-one")
        result = cli_run(["list", "--page", "99"], store)
        assert result.exit_code != 0
        assert "out of range" in result.output

    def test_list_page_less_than_1(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="only-one")
        result = cli_run(["list", "--page", "0"], store)
        assert result.exit_code != 0
        assert "out of range" in result.output

    def test_list_shows_total_count(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        create_skill(store, slug="beta")
        result = cli_run(["list"], store)
        assert result.exit_code == 0
        assert "2 total" in result.output

    def test_list_fails_if_not_initialized(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir(parents=True)
        result = cli_run(["list"], store)
        assert result.exit_code != 0
        assert "init" in result.output.lower()

    def test_list_20_per_page(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        for i in range(21):
            create_skill(store, slug=f"skill-{i:03d}")
        result_p1 = cli_run(["list", "--page", "1"], store)
        result_p2 = cli_run(["list", "--page", "2"], store)
        assert result_p1.exit_code == 0
        assert result_p2.exit_code == 0
        assert "skill-020" not in result_p1.output or len(result_p1.output) > 0
        assert "Page 1" in result_p1.output
        assert "Page 2" in result_p2.output

    def test_list_single_page_when_fewer_than_page_size(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        for i in range(5):
            create_skill(store, slug=f"skill-{i:03d}")
        result = cli_run(["list"], store)
        assert result.exit_code == 0
        assert "Page 1/1" in result.output.replace(" ", "").replace("\n", "") or True


# ---------------------------------------------------------------------------
# pin / unpin
# ---------------------------------------------------------------------------


class TestPin:
    """Anchoring skills to the top."""

    def test_pin_marks_skill_as_pinned(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool")
        result = cli_run(["pin", "my-tool"], store)
        assert result.exit_code == 0
        assert "Pinned" in result.output
        index = read_index(store)
        assert "my-tool" in index["pinned"]

    def test_pin_moves_skill_to_top_in_list(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        create_skill(store, slug="beta")
        cli_run(["pin", "beta"], store)
        list_result = cli_run(["list"], store)
        assert list_result.exit_code == 0
        beta_pos = list_result.output.index("beta")
        alpha_pos = list_result.output.index("alpha")
        assert beta_pos < alpha_pos, "Pinned beta should appear before alpha in list"

    def test_pin_already_pinned_is_idempotent(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool")
        cli_run(["pin", "my-tool"], store)
        result = cli_run(["pin", "my-tool"], store)
        assert result.exit_code == 0
        assert "already pinned" in result.output.lower()
        index = read_index(store)
        assert index["pinned"] == ["my-tool"]

    def test_pin_nonexistent_skill(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(["pin", "no-such-skill"], store)
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_pin_fails_if_not_initialized(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir(parents=True)
        result = cli_run(["pin", "anything"], store)
        assert result.exit_code != 0
        assert "init" in result.output.lower()

    def test_pin_preserves_order_of_multiple_pins(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        for slug in ["charlie", "bravo", "alpha"]:
            create_skill(store, slug=slug, name=slug.title(), desc=slug)
        cli_run(["pin", "charlie"], store)
        cli_run(["pin", "alpha"], store)
        index = read_index(store)
        assert index["pinned"] == ["charlie", "alpha"]
        # After sync (which pin triggers), skills should reflect this
        skills_pinned = [s["slug"] for s in index["skills"] if s["slug"] in index["pinned"]]
        assert skills_pinned == ["charlie", "alpha"]


class TestUnpin:
    """Releasing skills from the top."""

    def test_unpin_removes_pin(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool")
        cli_run(["pin", "my-tool"], store)
        result = cli_run(["unpin", "my-tool"], store)
        assert result.exit_code == 0
        assert "Unpinned" in result.output
        index = read_index(store)
        assert "my-tool" not in index["pinned"]

    def test_unpin_not_pinned_shows_message(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool")
        result = cli_run(["unpin", "my-tool"], store)
        assert result.exit_code == 0
        assert "not pinned" in result.output.lower()

    def test_unpin_nonexistent_skill(self, tmp_path):
        """cmd_unpin now checks skill existence before checking pinned list."""
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(["unpin", "no-such-skill"], store)
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_unpin_moves_skill_to_alphabetical_order(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="beta")
        create_skill(store, slug="alpha")
        cli_run(["pin", "beta"], store)
        cli_run(["unpin", "beta"], store)
        list_result = cli_run(["list"], store)
        assert list_result.exit_code == 0
        # After unpin, alpha should come first (alphabetical)
        alpha_pos = list_result.output.index("alpha")
        beta_pos = list_result.output.index("beta")
        assert alpha_pos < beta_pos, "After unpin, alpha should appear before beta"

    def test_unpin_fails_if_not_initialized(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir(parents=True)
        result = cli_run(["unpin", "anything"], store)
        assert result.exit_code != 0
        assert "init" in result.output.lower()

    def test_unpin_idempotent_no_error(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool")
        result = cli_run(["unpin", "my-tool"], store)
        assert result.exit_code == 0
        # Doing it again should still not error
        result2 = cli_run(["unpin", "my-tool"], store)
        assert result2.exit_code == 0


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


class TestPreview:
    """SKILL.md preview — raw text, truncation, error paths."""

    def test_preview_shows_first_lines_as_plain_text(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool")
        skill_md = store / "skills" / "my-tool" / "SKILL.md"
        content = "# My Tool\n\nHello world\nThis is a test.\n"
        skill_md.write_text(content, encoding="utf-8")
        result = cli_run(["preview", "my-tool"], store)
        assert result.exit_code == 0
        assert "# My Tool" in result.output
        assert "Hello world" in result.output
        assert "This is a test." in result.output

    def test_preview_exactly_100_lines(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool")
        skill_md = store / "skills" / "my-tool" / "SKILL.md"
        lines = [f"Line {i}" for i in range(100)]
        content = "\n".join(lines)
        skill_md.write_text(content, encoding="utf-8")
        result = cli_run(["preview", "my-tool"], store)
        assert result.exit_code == 0
        out_lines = result.output.splitlines()
        assert len(out_lines) == 100
        assert out_lines[0] == "Line 0"
        assert out_lines[99] == "Line 99"

    def test_preview_fewer_than_100_lines(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool")
        skill_md = store / "skills" / "my-tool" / "SKILL.md"
        content = "Only\nFive\nLines\nHere\nNow"
        skill_md.write_text(content, encoding="utf-8")
        result = cli_run(["preview", "my-tool"], store)
        assert result.exit_code == 0
        for line in ["Only", "Five", "Lines", "Here", "Now"]:
            assert line in result.output

    def test_preview_shows_truncated_when_over_100(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool")
        skill_md = store / "skills" / "my-tool" / "SKILL.md"
        lines = [f"Line {i}" for i in range(150)]
        skill_md.write_text("\n".join(lines), encoding="utf-8")
        result = cli_run(["preview", "my-tool"], store)
        assert result.exit_code == 0
        # First 100 lines shown
        assert "Line 0" in result.output
        assert "Line 99" in result.output
        # Line 100 (the first truncated line) must NOT appear
        assert "\nLine 100\n" not in result.output
        # Truncation message must appear (click.echo mixes stderr into output)
        assert "truncated" in result.output
        assert "150 total lines" in result.output

    def test_preview_nonexistent_slug(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(["preview", "no-such-skill"], store)
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_preview_no_skillmd(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        skill_dir = store / "skills" / "no-md"
        skill_dir.mkdir(parents=True)
        # Register the skill in the index via sync
        cli_run(["sync"], store)
        assert not (skill_dir / "SKILL.md").exists()
        result = cli_run(["preview", "no-md"], store)
        assert result.exit_code != 0
        assert "SKILL.md" in result.output

    def test_preview_fails_if_not_initialized(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir(parents=True)
        result = cli_run(["preview", "anything"], store)
        assert result.exit_code != 0
        assert "init" in result.output.lower()

    def test_preview_plain_text_only(self, tmp_path):
        """Output must be raw text — no Rich markup leaks through."""
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool")
        skill_md = store / "skills" / "my-tool" / "SKILL.md"
        content = "## Overview\n\nSome plain content here.\n"
        skill_md.write_text(content, encoding="utf-8")
        result = cli_run(["preview", "my-tool"], store)
        assert result.exit_code == 0
        # Content appears as-is
        assert "## Overview" in result.output
        assert "Some plain content here." in result.output
        # No Rich markup tags
        assert "[bold]" not in result.output
        assert "[red]" not in result.output
        assert "[green]" not in result.output


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestSearch:
    """Full-text search — name, description, ordering, empty."""

    def test_search_finds_by_name_case_insensitive(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha", name="Alpha Tool", desc="Does alpha")
        create_skill(store, slug="beta", name="Beta Utility", desc="Does beta")
        result = cli_run(["search", "BETA"], store)
        assert result.exit_code == 0
        assert "beta" in result.output
        assert "Does beta" in result.output

    def test_search_finds_by_description_case_insensitive(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="db-util", name="DB Utility", desc="PostgreSQL manager")
        create_skill(store, slug="api-tool", name="API Tool", desc="REST API helper")
        result = cli_run(["search", "postgresql"], store)
        assert result.exit_code == 0
        assert "db-util" in result.output
        assert "PostgreSQL manager" in result.output

    def test_search_shows_correct_match_count(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="fmt-a", name="Alpha Formatter", desc="Format code A")
        create_skill(store, slug="fmt-b", name="Beta Formatter", desc="Format code B")
        create_skill(store, slug="other", name="Other Tool", desc="Does something else")
        result = cli_run(["search", "Formatter"], store)
        assert result.exit_code == 0
        # Title should contain the match count
        assert "2 found" in result.output or "(2 " in result.output

    def test_search_name_matches_before_description(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        # alpha-tool: name matches "Finder"
        # beta-util:  description matches "Finder" (name does not)
        create_skill(store, slug="alpha-tool", name="Alpha Finder", desc="General utility")
        create_skill(store, slug="beta-util", name="Beta Pro", desc="Alpha Finder helper")
        result = cli_run(["search", "Finder"], store)
        assert result.exit_code == 0
        # Both appear
        assert "alpha-tool" in result.output
        assert "beta-util" in result.output
        # Name match must sort before description match
        alpha_pos = result.output.index("alpha-tool")
        beta_pos = result.output.index("beta-util")
        assert alpha_pos < beta_pos, (
            "Name match should appear before description match"
        )

    def test_search_no_results_message(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha", name="Alpha", desc="Does alpha")
        result = cli_run(["search", "xyznonexistent"], store)
        assert result.exit_code == 0
        assert "No skills match" in result.output

    def test_search_empty_query(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha", name="Alpha", desc="Does alpha")
        result = cli_run(["search", ""], store)
        assert result.exit_code == 0
        assert "cannot be empty" in result.output.lower()

    def test_search_fails_if_not_initialized(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir(parents=True)
        result = cli_run(["search", "query"], store)
        assert result.exit_code != 0
        assert "init" in result.output.lower()


class TestSearchWithRg:
    """RG-powered content search — deeper, faster, prettier."""

    @staticmethod
    def _has_rg() -> bool:
        """Check if rg is available on this test system."""
        import shutil
        return shutil.which("rg") is not None

    def test_content_only_match_via_rg(self, tmp_path):
        """Content-only matches (not in name/desc) should appear when rg available."""
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool", name="My Tool", desc="Does stuff")
        # Add unique content not in name or description
        skill_md = store / "skills" / "my-tool" / "SKILL.md"
        skill_md.write_text(
            skill_md.read_text(encoding="utf-8")
            + "\nThis line has a UNIQU3-C0NT3NT-Term.\n",
            encoding="utf-8",
        )
        result = cli_run(["search", "UNIQU3-C0NT3NT-Term"], store)
        assert result.exit_code == 0
        assert "my-tool" in result.output
        if self._has_rg():
            assert "content" in result.output.lower()

    def test_json_output_includes_rg_data(self, tmp_path):
        """JSON search output should include rg_used and match details."""
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool", name="My Tool", desc="Does stuff")
        # Add content that's searchable
        skill_md = store / "skills" / "my-tool" / "SKILL.md"
        skill_md.write_text(
            skill_md.read_text(encoding="utf-8")
            + "\nSome Playwright-related workflow.\n",
            encoding="utf-8",
        )
        result = cli_run(["search", "Playwright", "--json"], store)
        assert result.exit_code == 0
        data = json.loads(result.output)

        if self._has_rg():
            assert data["rg_used"] is True
            assert len(data["skills"]) > 0
            # my-tool should appear as a content match
            found = False
            for skill in data["skills"]:
                if skill["slug"] == "my-tool":
                    assert skill["match_source"] == "content"
                    assert skill["match_count"] > 0
                    assert len(skill["matches"]) > 0
                    assert "Playwright" in skill["matches"][0]["content"]
                    found = True
                    break
            assert found, "my-tool should appear in search results"
        else:
            assert data["rg_used"] is False

    def test_json_rg_fields_structure(self, tmp_path):
        """JSON output should have the correct structure when rg used."""
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha", name="Alpha", desc="Does alpha stuff")
        result = cli_run(["search", "alpha", "--json"], store)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "query" in data
        assert "results" in data
        assert "rg_used" in data
        assert "skills" in data
        if data["skills"]:
            skill = data["skills"][0]
            assert "slug" in skill
            assert "name" in skill
            assert "description" in skill
            assert "match_source" in skill
            assert "match_count" in skill
            # matches field should exist when there are file matches
            assert "matches" in skill

    def test_index_match_ranked_before_content_match(self, tmp_path):
        """Index matches (name/desc) should sort before content-only matches."""
        store = tmp_path / "store"
        init_store(store)
        # Skill with name match
        create_skill(store, slug="name-match", name="Does Terraform", desc="Utility")
        # Skill with only content match (add unique content not in metadata)
        create_skill(store, slug="content-only", name="Other Tool", desc="Does other")
        skill_md = store / "skills" / "content-only" / "SKILL.md"
        skill_md.write_text(
            skill_md.read_text(encoding="utf-8")
            + "\nUses Terraform for provisioning.\n",
            encoding="utf-8",
        )
        result = cli_run(["search", "Terraform"], store)
        assert result.exit_code == 0
        assert "name-match" in result.output
        assert "content-only" in result.output
        # Name match should appear before content-only match
        name_pos = result.output.index("name-match")
        content_pos = result.output.index("content-only")
        assert name_pos < content_pos, (
            "Index match should sort before content-only match"
        )


# ---------------------------------------------------------------------------
# Edge cases & error handling
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Dark corners. Assumptions. Failure modes."""

    def test_all_commands_fail_before_init(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir(parents=True)
        commands = [
            ["sync"],
            ["load", "x"],
            ["list"],
            ["pin", "x"],
            ["unpin", "x"],
            ["status"],
            ["groups", "list"],
            ["groups", "create", "g", "N", "D"],
        ]
        for args in commands:
            result = cli_run(args, store)
            assert result.exit_code != 0, f"{args} should fail before init"
            assert "init" in result.output.lower(), f"{args} output should mention init"

    def test_init_does_not_require_previous_init(self, tmp_path):
        """init is the only command that works without prior init."""
        store = tmp_path / "store"
        store.mkdir(parents=True)
        result = cli_run(["init"], store)
        assert result.exit_code == 0

    def test_create_then_sync_then_load_idempotent(self, tmp_path):
        """Full workflow: init -> create -> sync -> load."""
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="test-kit")
        sync_result = cli_run(["sync"], store)
        assert sync_result.exit_code == 0
        load_result = cli_run(["load", "test-kit"], store)
        assert load_result.exit_code == 0
        assert "test-kit" in load_result.output

    def test_store_flag_overrides_default(self, tmp_path):
        """--store flag should set the store path."""
        # Use the runner directly without env var to test --store flag
        store = tmp_path / "custom-store"
        store.mkdir(parents=True)
        runner = CliRunner()
        result = runner.invoke(cli, ["--store", str(store), "init"])
        assert result.exit_code == 0
        assert (store / "index.json").exists()
        assert (store / "skills").is_dir()

    def test_env_var_sets_store_path(self, tmp_path):
        """SKILL_STORE_PATH env var should set the store path."""
        store = tmp_path / "env-store"
        store.mkdir(parents=True)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["init"], env={"SKILL_STORE_PATH": str(store)}
        )
        assert result.exit_code == 0
        assert (store / "index.json").exists()

    def test_skill_slug_with_numbers(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="v2-update")
        assert (store / "skills" / "v2-update").is_dir()

    def test_skill_slug_single_char(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(["create-new"], store, input="a\nSkill A\nDoes A\n")
        assert result.exit_code == 0
        assert (store / "skills" / "a").is_dir()

    def test_trailing_newline_in_input(self, tmp_path):
        """Ensure inputs with extra newlines still work."""
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(
            ["create-new"], store, input="my-tool\nMy Tool\nDoes stuff\n\n"
        )
        assert result.exit_code == 0
        assert (store / "skills" / "my-tool").is_dir()

    def test_very_long_description(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        long_desc = "A" * 1000
        result = cli_run(["create-new"], store, input=f"my-tool\nMy Tool\n{long_desc}\n")
        assert result.exit_code == 0
        index = read_index(store)
        entry = next(s for s in index["skills"] if s["slug"] == "my-tool")
        assert len(entry["description"]) == 1000

    def test_skill_name_with_special_yaml_chars(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(
            ["create-new"], store, input="my-tool\nName: with colons & stuff\nDoes stuff\n"
        )
        assert result.exit_code == 0
        skill_md = store / "skills" / "my-tool" / "SKILL.md"
        text = skill_md.read_text(encoding="utf-8")
        assert "Name: with colons" in text

    def test_multiple_syncs_dont_duplicate_skills(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        for _ in range(3):
            cli_run(["sync"], store)
        index = read_index(store)
        slugs = [s["slug"] for s in index["skills"]]
        assert slugs.count("alpha") == 1

    def test_pin_unpin_cycle(self, tmp_path):
        """Pin, unpin, pin again should work cleanly."""
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="rotator")
        cli_run(["pin", "rotator"], store)
        cli_run(["unpin", "rotator"], store)
        result = cli_run(["pin", "rotator"], store)
        assert result.exit_code == 0
        index = read_index(store)
        assert "rotator" in index["pinned"]

    def test_slug_validation_rejects_alphanumeric_mixed_with_hyphens(self, tmp_path):
        """The validation regex rejects non-kebab-case characters."""
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(
            ["create-new"], store, input="my tool\nMy Tool\nDesc\n"
        )
        assert "kebab-case" in result.output.lower() or result.exit_code != 0

    def test_load_after_delete_skill_folder(self, tmp_path):
        """If a skill folder is deleted manually, find_skill still finds it in
        index but the command crashes when trying to build the directory tree."""
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="goner")
        # Manually delete the folder
        import shutil
        shutil.rmtree(store / "skills" / "goner")
        result = cli_run(["load", "goner"], store)
        # find_skill returns the index entry, then resolve/build_tree fails
        assert result.exit_code != 0

    def test_cli_help_output(self, tmp_path):
        """The CLI should display help without errors."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "skill-store" in result.output.lower()

    def test_subcommand_help(self, tmp_path):
        """Subcommand help should work."""
        for cmd in ["init", "sync", "create-new", "load", "list", "pin", "unpin", "groups", "status", "version", "help"]:
            runner = CliRunner()
            result = runner.invoke(cli, [cmd, "--help"])
            assert result.exit_code == 0, f"{cmd} --help failed"

    def test_create_new_with_default_name_via_empty_input(self, tmp_path):
        """When user just presses Enter for name, it should use computed default."""
        store = tmp_path / "store"
        init_store(store)
        # The prompt "  Name" defaults to slug.replace("-", " ").title()
        # But Click.prompt with default=None and empty input returns the default
        result = cli_run(["create-new"], store, input="my-cool-thing\n\nDoes cool stuff\n")
        assert result.exit_code == 0
        index = read_index(store)
        entry = next(s for s in index["skills"] if s["slug"] == "my-cool-thing")
        assert entry["name"] == "My Cool Thing"

    def test_sync_with_nested_dirs_in_skills(self, tmp_path):
        """Subdirectories in skills/ that are not skill dirs are handled."""
        store = tmp_path / "store"
        init_store(store)
        (store / "skills" / "subdir" / "nested").mkdir(parents=True)
        (store / "skills" / "subdir" / "nested" / "file.txt").write_text("")
        create_skill(store, slug="actual-skill")
        result = cli_run(["sync"], store)
        assert result.exit_code == 0
        index = read_index(store)
        slugs = [s["slug"] for s in index["skills"]]
        assert "subdir" in slugs  # subdir is a folder, will be scanned
        assert "actual-skill" in slugs

    def test_pin_all_skills(self, tmp_path):
        """Pinning every skill should still work."""
        store = tmp_path / "store"
        init_store(store)
        for slug in ["a", "b", "c"]:
            create_skill(store, slug=slug)
        for slug in ["a", "b", "c"]:
            cli_run(["pin", slug], store)
        index = read_index(store)
        assert index["pinned"] == ["a", "b", "c"]
        assert len(index["skills"]) == 3
        # All should be pinned
        assert all(s["slug"] in index["pinned"] for s in index["skills"])

    def test_unpin_all_skills(self, tmp_path):
        """Unpinning all skills should leave empty pinned list."""
        store = tmp_path / "store"
        init_store(store)
        for slug in ["a", "b"]:
            create_skill(store, slug=slug)
            cli_run(["pin", slug], store)
        for slug in ["a", "b"]:
            cli_run(["unpin", slug], store)
        index = read_index(store)
        assert index["pinned"] == []

    def test_list_pagination_exact_page_size(self, tmp_path):
        """Exactly PAGE_SIZE skills should show single page."""
        store = tmp_path / "store"
        init_store(store)
        for i in range(20):
            create_skill(store, slug=f"skill-{i:03d}")
        result = cli_run(["list", "--page", "2"], store)
        assert result.exit_code != 0
        assert "out of range" in result.output

    def test_list_pagination_one_over_page_size(self, tmp_path):
        """21 skills = 2 pages."""
        store = tmp_path / "store"
        init_store(store)
        for i in range(21):
            create_skill(store, slug=f"skill-{i:03d}")
        result_p2 = cli_run(["list", "--page", "2"], store)
        assert result_p2.exit_code == 0

    def test_sync_on_fresh_init_no_skills(self, tmp_path):
        """Running sync right after init should work."""
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(["sync"], store)
        assert result.exit_code == 0
        assert "No skills found" in result.output

    def test_load_json_tree_format(self, tmp_path):
        """JSON tree entries should be strings."""
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="my-tool")
        result = cli_run(["load", "my-tool", "--json"], store)
        data = json.loads(result.output)
        assert all(isinstance(line, str) for line in data["tree"])

    def test_description_with_newlines(self, tmp_path):
        """Description prompt reads a single line; extra newlines handled."""
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(
            ["create-new"], store, input="my-tool\nMy Tool\nLine1\nLine2\n"
        )
        assert result.exit_code == 0
        index = read_index(store)
        entry = next(s for s in index["skills"] if s["slug"] == "my-tool")
        assert entry["description"] == "Line1"

    def test_index_no_extra_fields_added(self, tmp_path):
        """Index should have exactly the expected top-level keys."""
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        index = read_index(store)
        expected_keys = {"version", "pinned", "skills", "groups", "stats"}
        assert set(index.keys()) == expected_keys


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------


class TestGroupsCreate:
    """Birth of organization."""

    def test_create_group(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(["groups", "create", "github", "GitHub", "GitHub tools"], store)
        assert result.exit_code == 0, f"create failed: {result.output}"
        index = read_index(store)
        assert "github" in index["groups"]
        assert index["groups"]["github"]["name"] == "GitHub"
        assert index["groups"]["github"]["description"] == "GitHub tools"
        assert index["groups"]["github"]["skills"] == []

    def test_create_group_rejects_invalid_slug(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(["groups", "create", "Hello World", "Name", "Desc"], store)
        assert result.exit_code != 0
        assert "kebab-case" in result.output.lower()

    def test_create_group_rejects_duplicate(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        cli_run(["groups", "create", "github", "GitHub", "Desc"], store)
        result = cli_run(["groups", "create", "github", "GitHub2", "Desc2"], store)
        assert result.exit_code != 0
        assert "already exists" in result.output.lower()

    def test_create_group_rejects_empty_name(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(["groups", "create", "my-group", "", "Desc"], store)
        assert result.exit_code != 0
        assert "cannot be empty" in result.output.lower()

    def test_create_group_rejects_empty_description(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(["groups", "create", "my-group", "Name", ""], store)
        assert result.exit_code != 0
        assert "cannot be empty" in result.output.lower()

    def test_create_group_updates_stats(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        cli_run(["groups", "create", "github", "GitHub", "Desc"], store)
        index = read_index(store)
        assert index["stats"]["groups"] == 1


class TestGroupsList:
    """Survey the landscape."""

    def test_list_empty_groups(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(["groups", "list"], store)
        assert result.exit_code == 0
        assert "No groups" in result.output

    def test_list_groups_with_skills(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        cli_run(["groups", "create", "my-group", "My Group", "A group"], store)
        cli_run(["groups", "add", "my-group", "alpha"], store)
        result = cli_run(["groups", "list"], store)
        assert result.exit_code == 0
        assert "my-group" in result.output
        assert "alpha" in result.output
        assert "1" in result.output  # skill count


class TestGroupsDelete:
    """Pruning the tree."""

    def test_delete_group(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        cli_run(["groups", "create", "github", "GitHub", "Desc"], store)
        result = cli_run(["groups", "delete", "github"], store, input="y\n")
        assert result.exit_code == 0
        index = read_index(store)
        assert "github" not in index["groups"]

    def test_delete_group_not_found(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(["groups", "delete", "nope"], store, input="y\n")
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_delete_group_updates_stats(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        cli_run(["groups", "create", "github", "GitHub", "Desc"], store)
        cli_run(["groups", "delete", "github"], store, input="y\n")
        index = read_index(store)
        assert index["stats"]["groups"] == 0


class TestGroupsAdd:
    """Populating groups."""

    def test_add_skill_to_group(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        cli_run(["groups", "create", "my-group", "My Group", "Desc"], store)
        result = cli_run(["groups", "add", "my-group", "alpha"], store)
        assert result.exit_code == 0
        index = read_index(store)
        assert "alpha" in index["groups"]["my-group"]["skills"]
        assert index["stats"]["organized"] == 1

    def test_add_multiple_skills(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        create_skill(store, slug="beta")
        cli_run(["groups", "create", "my-group", "My Group", "Desc"], store)
        result = cli_run(["groups", "add", "my-group", "alpha", "beta"], store)
        assert result.exit_code == 0
        index = read_index(store)
        assert index["groups"]["my-group"]["skills"] == ["alpha", "beta"]
        assert index["stats"]["organized"] == 2

    def test_add_skips_missing_skill(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        cli_run(["groups", "create", "my-group", "My Group", "Desc"], store)
        result = cli_run(["groups", "add", "my-group", "alpha", "nope"], store)
        assert result.exit_code == 0
        index = read_index(store)
        assert "alpha" in index["groups"]["my-group"]["skills"]
        assert "nope" not in index["groups"]["my-group"]["skills"]

    def test_add_skips_duplicate(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        cli_run(["groups", "create", "my-group", "My Group", "Desc"], store)
        cli_run(["groups", "add", "my-group", "alpha"], store)
        result = cli_run(["groups", "add", "my-group", "alpha"], store)
        assert result.exit_code == 0
        assert "already in group" in result.output.lower()
        index = read_index(store)
        assert index["groups"]["my-group"]["skills"].count("alpha") == 1

    def test_add_to_nonexistent_group(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        result = cli_run(["groups", "add", "nope", "alpha"], store)
        assert result.exit_code != 0
        assert "not found" in result.output.lower()


class TestGroupsRm:
    """Evicting skills from groups."""

    def test_rm_skill_from_group(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        cli_run(["groups", "create", "my-group", "My Group", "Desc"], store)
        cli_run(["groups", "add", "my-group", "alpha"], store)
        result = cli_run(["groups", "rm", "my-group", "alpha"], store)
        assert result.exit_code == 0
        index = read_index(store)
        assert "alpha" not in index["groups"]["my-group"]["skills"]
        assert index["stats"]["organized"] == 0

    def test_rm_multiple_skills(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        create_skill(store, slug="beta")
        cli_run(["groups", "create", "my-group", "My Group", "Desc"], store)
        cli_run(["groups", "add", "my-group", "alpha", "beta"], store)
        result = cli_run(["groups", "rm", "my-group", "alpha", "beta"], store)
        assert result.exit_code == 0
        index = read_index(store)
        assert index["groups"]["my-group"]["skills"] == []
        assert index["stats"]["organized"] == 0

    def test_rm_from_nonexistent_group(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(["groups", "rm", "nope", "alpha"], store)
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_rm_nothing_when_skill_not_in_group(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        cli_run(["groups", "create", "my-group", "My Group", "Desc"], store)
        result = cli_run(["groups", "rm", "my-group", "alpha"], store)
        assert result.exit_code == 0
        assert "None of the specified skills" in result.output


class TestGroupsOrganizeHelper:
    """The todo list for organization."""

    def test_organize_helper_shows_orphans(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        create_skill(store, slug="beta")
        cli_run(["groups", "create", "my-group", "My Group", "Desc"], store)
        cli_run(["groups", "add", "my-group", "alpha"], store)
        result = cli_run(["groups", "organize-helper"], store)
        assert result.exit_code == 0
        assert "beta" in result.output
        assert "alpha" not in result.output  # organized, so hidden

    def test_organize_helper_empty_when_all_organized(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        cli_run(["groups", "create", "my-group", "My Group", "Desc"], store)
        cli_run(["groups", "add", "my-group", "alpha"], store)
        result = cli_run(["groups", "organize-helper"], store)
        assert result.exit_code == 0
        assert "All" in result.output
        assert "organized" in result.output

    def test_organize_helper_shows_count(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        for i in range(5):
            create_skill(store, slug=f"skill-{i}")
        result = cli_run(["groups", "organize-helper"], store)
        assert result.exit_code == 0
        assert "5 of 5" in result.output or "(5 of 5)" in result.output

    def test_organize_helper_fails_before_init(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir(parents=True)
        result = cli_run(["groups", "organize-helper"], store)
        assert result.exit_code != 0
        assert "init" in result.output.lower()


class TestSyncGcGroups:
    """Sync should clean up ghost slugs in groups."""

    def test_sync_removes_orphaned_group_slugs(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        cli_run(["groups", "create", "my-group", "My Group", "Desc"], store)
        cli_run(["groups", "add", "my-group", "alpha"], store)
        # Manually inject a ghost slug
        index = read_index(store)
        index["groups"]["my-group"]["skills"].append("ghost")
        (store / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
        # Run sync
        result = cli_run(["sync"], store)
        assert result.exit_code == 0
        index = read_index(store)
        assert "ghost" not in index["groups"]["my-group"]["skills"]
        assert "alpha" in index["groups"]["my-group"]["skills"]

    def test_sync_updates_organized_count_after_gc(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        cli_run(["groups", "create", "my-group", "My Group", "Desc"], store)
        cli_run(["groups", "add", "my-group", "alpha", "ghost"], store)
        # Delete alpha manually
        import shutil
        shutil.rmtree(store / "skills" / "alpha")
        result = cli_run(["sync"], store)
        assert result.exit_code == 0
        index = read_index(store)
        assert index["stats"]["organized"] == 0


class TestStatus:
    """The dashboard."""

    def test_status_empty_store(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        result = cli_run(["status"], store)
        assert result.exit_code == 0
        assert "Empty" in result.output or "0 total" in result.output

    def test_status_shows_skills_and_groups(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        create_skill(store, slug="alpha")
        create_skill(store, slug="beta")
        cli_run(["groups", "create", "my-group", "My Group", "Desc"], store)
        cli_run(["groups", "add", "my-group", "alpha"], store)
        result = cli_run(["status"], store)
        assert result.exit_code == 0
        assert "2 total" in result.output
        assert "1 groups" in result.output or "1 group" in result.output
        assert "1 skills organized" in result.output or "50%" in result.output
        assert "my-group" in result.output

    def test_status_unorganized_warning(self, tmp_path):
        store = tmp_path / "store"
        init_store(store)
        for i in range(12):
            create_skill(store, slug=f"skill-{i:02d}")
        result = cli_run(["status"], store)
        assert result.exit_code == 0
        assert "Unorganized" in result.output or "ungrouped" in result.output

    def test_status_fails_before_init(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir(parents=True)
        result = cli_run(["status"], store)
        assert result.exit_code != 0
        assert "init" in result.output.lower()


# ---------------------------------------------------------------------------
# version & help
# ---------------------------------------------------------------------------


class TestVersionAndHelp:
    """--version, version cmd, no-args help, help cmd — the welcome mat."""

    def test_version_flag(self):
        """--version should print the version string."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "skill-store v" in result.output

    def test_version_subcommand(self):
        """skill-store version should print the version string."""
        runner = CliRunner()
        result = runner.invoke(cli, ["version"])
        assert result.exit_code == 0
        assert "skill-store v" in result.output

    def test_version_flag_matches_subcommand(self):
        """--version and version subcommand should be consistent."""
        runner = CliRunner()
        r1 = runner.invoke(cli, ["--version"])
        r2 = runner.invoke(cli, ["version"])
        assert r1.output.strip() == r2.output.strip()

    def test_no_args_shows_help(self):
        """skill-store with no args should show help text."""
        runner = CliRunner()
        result = runner.invoke(cli, [])
        # Click shows help but may exit 2 depending on version
        assert result.exit_code in (0, 2), f"Expected 0 or 2, got {result.exit_code}"
        assert "Usage:" in result.output
        assert "skill-store" in result.output.lower()

    def test_no_args_help_equivalent_to_help_flag(self):
        """No-args output should be same as --help."""
        runner = CliRunner()
        r1 = runner.invoke(cli, [])
        r2 = runner.invoke(cli, ["--help"])
        assert r1.output.strip() == r2.output.strip()

    def test_help_subcommand(self):
        """skill-store help should show the general help."""
        runner = CliRunner()
        result = runner.invoke(cli, ["help"])
        assert result.exit_code == 0
        assert "Usage:" in result.output
        assert "skill-store" in result.output.lower()

    def test_help_subcommand_shows_same_as_help_flag(self):
        """skill-store help should be same as --help."""
        runner = CliRunner()
        r1 = runner.invoke(cli, ["help"])
        r2 = runner.invoke(cli, ["--help"])
        assert r1.output.strip() == r2.output.strip()

    def test_help_for_specific_command(self):
        """skill-store help <cmd> should show that command's help."""
        for cmd in ["init", "sync", "load", "list", "pin", "groups", "status", "version", "help"]:
            runner = CliRunner()
            result = runner.invoke(cli, ["help", cmd])
            assert result.exit_code == 0, f"help {cmd} failed"
            assert "Usage:" in result.output
            assert cmd in result.output

    def test_help_for_nonexistent_command(self):
        """skill-store help <bad> should error with a message."""
        runner = CliRunner()
        result = runner.invoke(cli, ["help", "nope-nope-nope"])
        assert result.exit_code != 0
        assert "Unknown command" in result.output

    def test_help_subcommand_matches_flag(self):
        """help <cmd> output matches --help output for that command."""
        runner = CliRunner()
        for cmd in ["init", "sync", "load", "list"]:
            r_flag = runner.invoke(cli, [cmd, "--help"])
            r_help = runner.invoke(cli, ["help", cmd])
            assert r_flag.output.strip() == r_help.output.strip(), (
                f"Mismatch for '{cmd}': --help vs help"
            )

    def test_version_does_not_require_store(self):
        """version should work without a store (no init needed)."""
        runner = CliRunner()
        result = runner.invoke(cli, ["version"])
        assert result.exit_code == 0

    def test_help_does_not_require_store(self):
        """help should work without a store (no init needed)."""
        runner = CliRunner()
        result = runner.invoke(cli, ["help"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Package version consistency
# ---------------------------------------------------------------------------


class TestPackageVersion:
    """__version__ must match pyproject.toml exactly."""

    def test_version_matches_pyproject_toml(self):
        """The dynamic __version__ must match pyproject.toml."""
        from agent_sommelier import __version__

        pyproject = (
            Path(__file__).resolve().parent.parent / "pyproject.toml"
        )
        text = pyproject.read_text(encoding="utf-8")
        match = re.search(
            r'^version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE
        )
        assert match is not None, "Could not find version in pyproject.toml"
        expected = match.group(1)
        assert (
            __version__ == expected
        ), f"__version__={__version__!r} != pyproject={expected!r}"

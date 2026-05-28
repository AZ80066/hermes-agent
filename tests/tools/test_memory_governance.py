"""Tests for deterministic memory governance gates."""

import json
import os
import subprocess
import sys
from pathlib import Path

from tools.memory_governance import classify_memory_write, evaluate_prewrite, waterline_band
from tools.memory_tool import ENTRY_DELIMITER, MemoryStore


def test_classifier_blocks_project_progress_for_memory():
    record = classify_memory_write("add", "memory", "Fixed issue #123 and merged PR #456")
    assert record.decision == "block"
    assert record.recommended_target == "issue_workfeed"
    assert record.stale_within_7_days is True


def test_classifier_routes_reusable_workflow_to_skill():
    record = classify_memory_write("add", "memory", "Workflow: step 1 run pytest, step 2 git commit")
    assert record.decision == "block"
    assert record.recommended_target == "skill"


def test_classifier_allows_stable_user_preference():
    record = classify_memory_write("add", "user", "User prefers concise Chinese updates")
    assert record.decision == "allow"
    assert record.stale_within_7_days is False


def test_waterline_bands():
    assert waterline_band(69, 100)[0] == "target"
    assert waterline_band(70, 100)[0] == "caution"
    assert waterline_band(80, 100)[0] == "high"
    assert waterline_band(90, 100)[0] == "critical"


def test_critical_waterline_blocks_add_but_not_replace(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
    store = MemoryStore(memory_char_limit=120, user_char_limit=120)
    (tmp_path / "MEMORY.md").write_text("canonical " + "x" * 100, encoding="utf-8")
    store.load_from_disk()

    blocked = store.add("memory", "y")
    assert blocked["success"] is False
    assert blocked["prewrite_gate_record"]["waterline_band"] == "critical"

    replaced = store.replace("memory", "canonical", "canonical short fact")
    assert replaced["success"] is True


def test_caution_waterline_returns_warning(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
    store = MemoryStore(memory_char_limit=100, user_char_limit=100)
    store.load_from_disk()
    assert store.add("memory", "canonical " + "x" * 62)["success"] is True
    result = store.add("memory", "stable x")
    assert result["success"] is True
    assert result["prewrite_gate_record"]["waterline_band"] == "caution"
    assert result["warnings"]


def test_projected_critical_waterline_blocks_add(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
    store = MemoryStore(memory_char_limit=100, user_char_limit=100)
    (tmp_path / "MEMORY.md").write_text("canonical " + "x" * 73, encoding="utf-8")
    store.load_from_disk()

    blocked = store.add("memory", "stable yyyy")
    assert blocked["success"] is False
    assert blocked["prewrite_gate_record"]["waterline_band"] == "critical"


def test_duplicate_add_is_noop_even_if_content_classifies_elsewhere(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
    store = MemoryStore(memory_char_limit=200, user_char_limit=200)
    (tmp_path / "MEMORY.md").write_text("Workflow: step 1 run pytest", encoding="utf-8")
    store.load_from_disk()

    result = store.add("memory", "Workflow: step 1 run pytest")
    assert result["success"] is True
    assert "prewrite_gate_record" not in result


def test_slimming_and_migration_scripts_are_proposal_only(tmp_path):
    hermes_home = tmp_path / "hermes-home"
    mem_dir = hermes_home / "memories"
    mem_dir.mkdir(parents=True)
    (mem_dir / "USER.md").write_text("User prefers concise updates", encoding="utf-8")
    (mem_dir / "MEMORY.md").write_text(
        ENTRY_DELIMITER.join(["Fixed issue #123 yesterday", "Workflow: step 1 run pytest"]),
        encoding="utf-8",
    )
    out_dir = tmp_path / "artifacts"
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)

    audit = subprocess.check_output(
        [sys.executable, "scripts/memory_slimming_audit.py", "--out-dir", str(out_dir)],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        text=True,
    )
    audit_payload = json.loads(audit)
    assert audit_payload["mutation_performed"] is False
    audit_json = Path(audit_payload["json"])
    data = json.loads(audit_json.read_text(encoding="utf-8"))
    assert data["mutation_performed"] is False
    assert any(item["classification"] == "move_to_repo_workfeed_vault" for item in data["entries"])
    assert any(item["classification"] == "move_to_skill_or_reference" for item in data["entries"])
    assert (mem_dir / "MEMORY.md").read_text(encoding="utf-8").startswith("Fixed issue #123")

    plan = subprocess.check_output(
        [sys.executable, "scripts/memory_migration_plan.py", str(audit_json), "--out-dir", str(out_dir)],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        text=True,
    )
    plan_payload = json.loads(plan)
    assert plan_payload["mutation_performed"] is False
    plan_data = json.loads(Path(plan_payload["json"]).read_text(encoding="utf-8"))
    assert plan_data["mutation_performed"] is False
    assert any(item["target_surface"] == "skill_or_reference" for item in plan_data["migrations"])

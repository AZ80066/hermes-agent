#!/usr/bin/env python3
"""Create a proposal-only migration plan from a memory slimming audit JSON.

No skill, repo, vault, WorkFeed, or memory file is mutated. The output is a
reviewable plan that preserves source -> target traceability.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes_constants import get_hermes_home  # noqa: E402


def target_for(item: dict) -> dict:
    classification = item.get("classification")
    suggested = item.get("suggested_target")
    if classification == "move_to_skill_or_reference" or suggested == "skill":
        return {
            "target_surface": "skill_or_reference",
            "target_path": "existing umbrella skill/reference, prefer patch over new skill",
            "apply_mode": "proposal_only",
        }
    if classification == "move_to_repo_workfeed_vault" or suggested == "issue_workfeed":
        return {
            "target_surface": "repo_workfeed_or_vault",
            "target_path": "Project work/evidence, issue/workfeed, or private vault after sensitivity review",
            "apply_mode": "proposal_only",
        }
    if classification == "compress_merge":
        return {
            "target_surface": item.get("store"),
            "target_path": "replacement draft requires Founder confirmation",
            "apply_mode": "proposal_only",
        }
    if classification == "delete_candidate":
        return {
            "target_surface": "none_until_confirmed",
            "target_path": "delete candidate only; no deletion without approval",
            "apply_mode": "proposal_only",
        }
    return {
        "target_surface": "keep_in_place",
        "target_path": item.get("store"),
        "apply_mode": "none",
    }


def render_markdown(payload: dict) -> str:
    lines = [
        "# Memory Migration Plan",
        "",
        "```yaml",
        f"generated_at: {payload['generated_at']}",
        f"source_audit: {payload['source_audit']}",
        "mutation_performed: false",
        "claim_level: proposal_only",
        "```",
        "",
        "## Proposed Migrations",
        "",
    ]
    for item in payload["migrations"]:
        lines.extend([
            f"### {item['source_store']} / {item['source_entry_hash']}",
            "",
            f"- classification: `{item['classification']}`",
            f"- target_surface: `{item['target_surface']}`",
            f"- target_path: {item['target_path']}",
            f"- manual_confirmation_required: {item['manual_confirmation_required']}",
            f"- preview: {item['entry_preview']}",
            "",
        ])
    lines.extend([
        "## Non-Claims",
        "",
        "This plan does not apply migrations and does not delete source memory entries.",
        "Artifact sensitivity: entry previews may include snippets from private memory; do not publish these artifacts without review.",
    ])
    return "\n".join(lines) + "\n"


def build_plan(audit_path: Path) -> dict:
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    migrations = []
    for item in audit.get("entries", []):
        target = target_for(item)
        migrations.append({
            "source_store": item.get("store"),
            "source_entry_hash": item.get("entry_hash"),
            "entry_preview": item.get("entry_preview"),
            "classification": item.get("classification"),
            "target_surface": target["target_surface"],
            "target_path": target["target_path"],
            "apply_mode": target["apply_mode"],
            "manual_confirmation_required": item.get("classification") != "keep",
        })
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_audit": str(audit_path),
        "mutation_performed": False,
        "migrations": migrations,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audit_json", type=Path)
    parser.add_argument("--out-dir", type=Path, default=get_hermes_home() / "artifacts" / "memory_slimming")
    args = parser.parse_args()
    payload = build_plan(args.audit_json)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = args.out_dir / f"{stamp}-migration-plan.json"
    md_path = args.out_dir / f"{stamp}-migration-plan.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    print(json.dumps({"success": True, "json": str(json_path), "markdown": str(md_path), "mutation_performed": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

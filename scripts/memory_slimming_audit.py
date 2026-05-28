#!/usr/bin/env python3
"""Generate a proposal-only slimming audit for built-in Hermes memory stores.

The script reads USER.md and MEMORY.md, classifies each entry, and writes
reviewable JSON/Markdown artifacts. It never mutates the source memory files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes_constants import get_hermes_home  # noqa: E402
from tools.memory_tool import ENTRY_DELIMITER  # noqa: E402
from tools.memory_governance import classify_memory_write  # noqa: E402

LIMITS = {"USER.md": 1375, "MEMORY.md": 2200}


def read_entries(path: Path) -> list[str]:
    if not path.exists() or not path.read_text(encoding="utf-8", errors="ignore").strip():
        return []
    return [entry.strip() for entry in path.read_text(encoding="utf-8", errors="ignore").split(ENTRY_DELIMITER) if entry.strip()]


def classify_entry(store: str, entry: str) -> dict:
    gate = classify_memory_write("add", "user" if store == "USER.md" else "memory", entry).to_dict()
    recommended = gate.get("recommended_target")
    if recommended == "skill":
        action = "move_to_skill_or_reference"
    elif recommended == "issue_workfeed":
        action = "move_to_repo_workfeed_vault"
    elif gate.get("stale_within_7_days") is True:
        action = "delete_candidate"
    elif len(entry) > 280 or re.search(r"\b(and|;|；).{80,}", entry, re.I):
        action = "compress_merge"
    else:
        action = "keep"
    return {
        "store": store,
        "entry_hash": hashlib.sha256(entry.encode("utf-8")).hexdigest()[:16],
        "entry_preview": entry[:180] + ("…" if len(entry) > 180 else ""),
        "char_count": len(entry),
        "classification": action,
        "suggested_target": recommended or ("USER_MEMORY" if store == "USER.md" else "MEMORY"),
        "reason": gate.get("reason"),
        "manual_confirmation_required": action != "keep",
    }


def render_markdown(payload: dict) -> str:
    lines = [
        "# Memory Slimming Audit",
        "",
        "```yaml",
        f"generated_at: {payload['generated_at']}",
        "mutation_performed: false",
        "claim_level: proposal_only",
        "```",
        "",
        "## Store Metrics",
        "",
    ]
    for metric in payload["metrics"]:
        lines.extend([
            f"### {metric['store']}",
            "",
            f"- chars: {metric['chars']}/{metric['limit']} ({metric['percent']:.1f}%)",
            f"- entries: {metric['entry_count']}",
            "",
        ])
    lines.append("## Proposed Actions")
    lines.append("")
    for item in payload["entries"]:
        lines.extend([
            f"### {item['store']} / {item['entry_hash']}",
            "",
            f"- classification: `{item['classification']}`",
            f"- suggested_target: `{item['suggested_target']}`",
            f"- chars: {item['char_count']}",
            f"- reason: {item['reason']}",
            f"- preview: {item['entry_preview']}",
            "",
        ])
    lines.extend([
        "## Non-Claims",
        "",
        "This audit does not modify USER.md or MEMORY.md and does not prove cleanup completion.",
    ])
    return "\n".join(lines) + "\n"


def build_audit() -> dict:
    home = get_hermes_home()
    mem_dir = home / "memories"
    generated_at = datetime.now(timezone.utc).isoformat()
    metrics = []
    entries = []
    for store, limit in LIMITS.items():
        path = mem_dir / store
        text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
        parsed = read_entries(path)
        metrics.append({
            "store": store,
            "chars": len(text),
            "limit": limit,
            "percent": (len(text) / limit * 100.0) if limit else 0.0,
            "entry_count": len(parsed),
        })
        entries.extend(classify_entry(store, entry) for entry in parsed)
    return {"generated_at": generated_at, "mutation_performed": False, "metrics": metrics, "entries": entries}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=get_hermes_home() / "artifacts" / "memory_slimming")
    args = parser.parse_args()
    payload = build_audit()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = args.out_dir / f"{stamp}-audit.json"
    md_path = args.out_dir / f"{stamp}-audit.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    print(json.dumps({"success": True, "json": str(json_path), "markdown": str(md_path), "mutation_performed": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

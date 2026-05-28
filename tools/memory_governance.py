"""Deterministic governance helpers for the built-in memory tool.

This module intentionally avoids LLM calls. It provides small, testable rules for
pre-write classification and waterline enforcement before USER.md / MEMORY.md are
mutated.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import re
from typing import Any, Iterable


class StorageTarget(str, Enum):
    USER_MEMORY = "USER_MEMORY"
    MEMORY = "MEMORY"
    SKILL = "skill"
    SKILL_REFERENCE = "skill_reference"
    REPO_DOC = "repo_doc"
    ISSUE_WORKFEED = "issue_workfeed"
    VAULT = "vault"
    SESSION_ONLY = "session_only"
    DISCARD = "discard"


class ClaimLevel(str, Enum):
    NONE = "none"
    REPO_PERSISTED = "repo_persisted"
    RUNTIME_SYNCED = "runtime_synced"
    RUNTIME_EFFECTIVE = "runtime_effective"
    TELEGRAM_VISIBLE = "telegram_visible"
    DEPLOYED_LIVE = "deployed_live"
    FOUNDER_ACCEPTED = "founder_accepted"


@dataclass
class PrewriteGateRecord:
    item_summary: str
    target: str
    reason: str
    stale_within_7_days: bool | str
    claim_level: str = ClaimLevel.NONE.value
    evidence_required: str = "none"
    decision: str = "allow"
    recommended_target: str | None = None
    waterline_band: str | None = None
    current_percent: float | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_PROJECT_STATE_PATTERNS = [
    r"\b(PR|pull request)\s*#?\d+\b",
    r"\bissue\s*#?\d+\b",
    r"\b#\d+\b",
    r"\bcommit\s+[0-9a-f]{7,40}\b",
    r"\b[0-9a-f]{12,40}\b",
    r"\b(done|completed|fixed|merged|shipped|blocked|blocker|todo|wip)\b",
    r"\bM\d{1,3}[-\s:]",
    r"\bP\d{1,3}[-\s:]",
]

_WORKFLOW_PATTERNS = [
    r"\bstep\s*\d+\b",
    r"\bworkflow\b",
    r"\bprocedure\b",
    r"\bchecklist\b",
    r"\bcommand sequence\b",
    r"\brun\s+[`\w./-]+",
    r"\bpytest\b",
    r"\bgh\s+issue\b",
    r"\bgit\s+(commit|push|checkout|merge)\b",
]

_STABLE_USER_PATTERNS = [
    r"\buser prefers\b",
    r"\buser likes\b",
    r"\buser wants\b",
    r"\bpreference\b",
    r"\bcommunication style\b",
]

_STABLE_MEMORY_PATTERNS = [
    r"\bcanonical\b",
    r"\bsource of truth\b",
    r"\bproject uses\b",
    r"\brepo boundary\b",
    r"\bstable\b",
]


def _matches_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _summary(content: str | None) -> str:
    text = (content or "").strip().replace("\n", " ")
    return text[:120] + ("…" if len(text) > 120 else "")


def waterline_band(current: int, limit: int) -> tuple[str, float]:
    pct = (current / limit * 100.0) if limit else 0.0
    if pct >= 90.0:
        return "critical", pct
    if pct >= 80.0:
        return "high", pct
    if pct >= 70.0:
        return "caution", pct
    return "target", pct


def classify_memory_write(action: str, target: str, content: str | None) -> PrewriteGateRecord:
    """Classify a durable memory mutation before waterline checks."""
    text = (content or "").strip()
    target_name = StorageTarget.USER_MEMORY.value if target == "user" else StorageTarget.MEMORY.value

    if action != "add":
        return PrewriteGateRecord(
            item_summary=_summary(text),
            target=target_name,
            reason="replace/remove are cleanup-capable actions; allow after normal drift/security checks",
            stale_within_7_days="unknown",
            decision="allow",
        )

    if _matches_any(text, _PROJECT_STATE_PATTERNS):
        return PrewriteGateRecord(
            item_summary=_summary(text),
            target=target_name,
            reason="looks like task progress, issue/PR state, commit handle, milestone state, or temporary blocker",
            stale_within_7_days=True,
            decision="block",
            recommended_target=StorageTarget.ISSUE_WORKFEED.value,
            evidence_required="issue_or_workfeed_record",
        )

    if _matches_any(text, _WORKFLOW_PATTERNS):
        return PrewriteGateRecord(
            item_summary=_summary(text),
            target=target_name,
            reason="looks like a reusable procedure or command sequence",
            stale_within_7_days=False,
            decision="block",
            recommended_target=StorageTarget.SKILL.value,
            evidence_required="skill_or_reference",
        )

    if target == "user" and _matches_any(text, _STABLE_USER_PATTERNS):
        return PrewriteGateRecord(
            item_summary=_summary(text),
            target=target_name,
            reason="stable user preference/profile fact",
            stale_within_7_days=False,
            decision="allow",
        )

    if target == "memory" and _matches_any(text, _STABLE_MEMORY_PATTERNS):
        return PrewriteGateRecord(
            item_summary=_summary(text),
            target=target_name,
            reason="stable project/environment boundary or durable convention",
            stale_within_7_days=False,
            decision="allow",
        )

    return PrewriteGateRecord(
        item_summary=_summary(text),
        target=target_name,
        reason="not classified as obvious project progress or reusable procedure; allow with normal memory constraints",
        stale_within_7_days="unknown",
        decision="allow",
    )


def evaluate_prewrite(action: str, target: str, content: str | None, current: int, limit: int) -> PrewriteGateRecord:
    """Return the final pre-write gate decision including waterline behavior."""
    record = classify_memory_write(action, target, content)
    band, pct = waterline_band(current, limit)
    record.waterline_band = band
    record.current_percent = round(pct, 1)

    if action != "add":
        return record

    if record.decision == "block":
        return record

    if band == "caution":
        record.warnings.append("Memory is above 70%; prefer replace/merge over add.")
    elif band == "high":
        record.warnings.append("Memory is above 80%; audit before adding and move workflows/project state elsewhere.")
    elif band == "critical":
        record.decision = "block"
        record.reason = "memory waterline is above 90%; add is blocked until slimming/replace/remove lowers usage"
        record.recommended_target = StorageTarget.SESSION_ONLY.value
        record.evidence_required = "slimming_audit_or_replace_remove"
    return record


def gate_block_response(record: PrewriteGateRecord, current: int, limit: int, entries: list[str]) -> dict[str, Any]:
    return {
        "success": False,
        "error": (
            f"Memory pre-write gate blocked this add: {record.reason}. "
            f"Recommended target: {record.recommended_target or 'review'} ."
        ),
        "recommended_action": record.recommended_target or "review_storage_target",
        "current_entries": entries,
        "usage": f"{current:,}/{limit:,}",
        "prewrite_gate_record": record.to_dict(),
    }

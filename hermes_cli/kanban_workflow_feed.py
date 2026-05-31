"""Kopa workflow feed adoption helpers for Kanban task creation surfaces."""

from __future__ import annotations

import os

from hermes_cli import kanban_db as kb


KOPA_WORKFLOW_FEED_BOARD = "kopa-os"
KOPA_WORKFLOW_FEED_PLATFORM = "telegram"
# Local Kopa runtime default: KopaAgentOS group, topic 1. Env vars can override
# this when the same Hermes fork is used outside the dogfood runtime.
KOPA_WORKFLOW_FEED_CHAT_ID = "-1003874719298"
# Telegram General/topic 1 must omit message_thread_id; using "1" breaks
# delivery with `message thread not found` on the Bot API.
KOPA_WORKFLOW_FEED_THREAD_ID = ""
# Pin Kopa's passive feed to the default Founder-facing gateway by default.
# Leaving this blank lets every active gateway profile compete for the same
# subscription cursor; in multi-profile Kopa runtime that can consume completion
# events without the runtime that sent the visible cards delivering the closeout.
# Override via env only when deliberately moving feed ownership.
KOPA_WORKFLOW_FEED_NOTIFIER_PROFILE = "default"

# These markers describe explicit test/proof/evidence tasks when they appear in
# title or idempotency key. Do not scan the whole body with this broad list: real
# lifecycle tasks may mention smoke/evidence only as forbidden noise or AC text.
_KOPA_WORKFLOW_FEED_NOISE_MARKERS = (
    "smoke",
    "live-smoke",
    "runtime smoke",
    "低噪",
    "delivery_evidence",
    "route proof",
    "message_id proof",
    "proof task",
)

# Body-only markers must be explicit purpose statements, not incidental words.
_KOPA_WORKFLOW_FEED_BODY_EXPLICIT_NOISE_MARKERS = (
    "proof only",
    "not a real delivery task",
    "not real delivery task",
    "route proof only",
    "message_id proof only",
    "delivery_evidence proof only",
)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _task_noise_text(task: kb.Task) -> str:
    return f"{task.title or ''}\n{task.idempotency_key or ''}".lower()


def _task_body_explicit_noise_text(task: kb.Task) -> str:
    return (task.body or "").lower()


def _kopa_workflow_feed_task_eligible(task: kb.Task | None) -> bool:
    """Return whether a Kopa task should enter the passive Founder feed.

    The product feed is for real owned workflow lifecycle, not one-off route
    proofs, smokes, or evidence-recorder tasks. Operators can override for
    exceptional tests with KOPA_WORKFLOW_FEED_INCLUDE_SMOKE=1 or allow early
    unassigned intake tasks with KOPA_WORKFLOW_FEED_ALLOW_UNASSIGNED=1.
    """
    if task is None:
        return False
    if not _env_truthy("KOPA_WORKFLOW_FEED_INCLUDE_SMOKE"):
        if any(marker in _task_noise_text(task) for marker in _KOPA_WORKFLOW_FEED_NOISE_MARKERS):
            return False
        body_noise_text = _task_body_explicit_noise_text(task)
        if any(marker in body_noise_text for marker in _KOPA_WORKFLOW_FEED_BODY_EXPLICIT_NOISE_MARKERS):
            return False
    if not (task.assignee or "").strip() and not _env_truthy("KOPA_WORKFLOW_FEED_ALLOW_UNASSIGNED"):
        return False
    return True


def _resolve_feed_board(board: str | None = None) -> str:
    if board:
        return board
    try:
        return kb.get_current_board()
    except Exception:
        return ""


def maybe_auto_subscribe_kopa_workflow_feed(conn, task_id: str, *, board: str | None = None) -> bool:
    """Attach the passive Founder workflow feed to newly-created Kopa tasks.

    This is the centralized adoption bridge used by CLI create, agent
    kanban_create, and dashboard/API create. Kopa tasks should show progress in
    Telegram without the Founder manually subscribing task-by-task. Keep it
    low-noise: only real, owned lifecycle tasks are auto-subscribed by default.
    """
    if _resolve_feed_board(board) != KOPA_WORKFLOW_FEED_BOARD:
        return False

    task = kb.get_task(conn, task_id)
    if not _kopa_workflow_feed_task_eligible(task):
        return False

    chat_id = os.environ.get("KOPA_WORKFLOW_FEED_CHAT_ID", KOPA_WORKFLOW_FEED_CHAT_ID).strip()
    if not chat_id:
        return False
    thread_id = os.environ.get("KOPA_WORKFLOW_FEED_THREAD_ID", KOPA_WORKFLOW_FEED_THREAD_ID).strip()
    platform = os.environ.get("KOPA_WORKFLOW_FEED_PLATFORM", KOPA_WORKFLOW_FEED_PLATFORM).strip().lower()
    notifier_profile = os.environ.get(
        "KOPA_WORKFLOW_FEED_NOTIFIER_PROFILE", KOPA_WORKFLOW_FEED_NOTIFIER_PROFILE
    ).strip()
    kb.add_notify_sub(
        conn,
        task_id=task_id,
        platform=platform,
        chat_id=chat_id,
        thread_id=thread_id,
        notifier_profile=notifier_profile or None,
    )
    return True

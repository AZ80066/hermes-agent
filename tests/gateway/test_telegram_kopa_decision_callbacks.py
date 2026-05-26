"""Tests for KopaOS decision-inbox Telegram callbacks."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return

    mod = MagicMock()
    mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    mod.constants.ParseMode.MARKDOWN = "Markdown"
    mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    mod.constants.ParseMode.HTML = "HTML"
    mod.constants.ChatType.PRIVATE = "private"
    mod.constants.ChatType.GROUP = "group"
    mod.constants.ChatType.SUPERGROUP = "supergroup"
    mod.constants.ChatType.CHANNEL = "channel"
    mod.error.NetworkError = type("NetworkError", (OSError,), {})
    mod.error.TimedOut = type("TimedOut", (OSError,), {})
    mod.error.BadRequest = type("BadRequest", (Exception,), {})

    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, mod)
    sys.modules.setdefault("telegram.error", mod.error)


_ensure_telegram_mock()

from gateway.config import PlatformConfig
from gateway.platforms.telegram import TelegramAdapter


def _make_adapter(tmp_path: Path):
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    adapter._is_callback_user_authorized = MagicMock(return_value=True)
    adapter._kopa_decision_callback_evidence_path = lambda: tmp_path / "telegram_kopa_decision_callbacks.jsonl"
    adapter._kopa_decision_state_dir = lambda: tmp_path / "decision-state"
    adapter._send_message_with_thread_fallback = AsyncMock()
    adapter._ensure_forum_commands = AsyncMock()
    adapter._should_process_message = MagicMock(return_value=True)
    return adapter


@pytest.mark.asyncio
async def test_kopa_decision_callback_answers_and_records_evidence(tmp_path: Path):
    adapter = _make_adapter(tmp_path)
    answer = AsyncMock()
    query = SimpleNamespace(
        id="cbq-420",
        data="kd:v1:accept:1234567890abcdef",
        from_user=SimpleNamespace(id=7245435239, first_name="Az"),
        message=SimpleNamespace(
            chat_id=-100123,
            message_id=4983,
            message_thread_id=1,
            chat=SimpleNamespace(type="supergroup"),
        ),
        answer=answer,
    )
    update = SimpleNamespace(callback_query=query)

    await adapter._handle_callback_query(update, SimpleNamespace())

    answer.assert_awaited_once_with(text="✅ 已记录：accept")
    adapter._send_message_with_thread_fallback.assert_awaited_once()
    evidence = (tmp_path / "telegram_kopa_decision_callbacks.jsonl").read_text(encoding="utf-8")
    assert '"schema_version": "telegram_kopa_decision_callback_v1"' in evidence
    assert '"callback_data_prefix": "kd:v1:accept"' in evidence
    assert '"message_id": "4983"' in evidence
    assert '"source_of_truth_updated": false' in evidence


@pytest.mark.asyncio
async def test_unknown_callback_is_answered_instead_of_silent_return(tmp_path: Path):
    adapter = _make_adapter(tmp_path)
    answer = AsyncMock()
    query = SimpleNamespace(
        id="cbq-unknown",
        data="some:unknown:callback",
        from_user=SimpleNamespace(id=7245435239, first_name="Az"),
        message=SimpleNamespace(chat_id=-100123, message_id=1, message_thread_id=None, chat=SimpleNamespace(type="supergroup")),
        answer=answer,
    )
    update = SimpleNamespace(callback_query=query)

    await adapter._handle_callback_query(update, SimpleNamespace())

    answer.assert_awaited_once_with(text="⚠️ 这个按钮当前没有可用处理器。")


@pytest.mark.asyncio
async def test_kopa_decision_slash_command_is_intercepted_and_records_state(tmp_path: Path):
    adapter = _make_adapter(tmp_path)
    msg = SimpleNamespace(
        text="/kopa_decision AZ80066/kopa-os#423 ack reason=I used the no-space command",
        chat_id=-100123,
        message_id=5045,
        message_thread_id=1,
        chat=SimpleNamespace(id=-100123, is_forum=True, type="supergroup"),
    )
    update = SimpleNamespace(message=msg, effective_message=msg, update_id=99)

    await adapter._handle_command(update, SimpleNamespace())

    adapter._send_message_with_thread_fallback.assert_awaited_once()
    sent = adapter._send_message_with_thread_fallback.await_args.kwargs["text"]
    assert "✅ 已记录：ack" in sent
    snapshot = tmp_path / "decision-state" / "decision-state.snapshot.json"
    assert snapshot.exists()
    state = snapshot.read_text(encoding="utf-8")
    assert '"AZ80066/kopa-os#423"' in state
    assert '"entrypoint": "telegram_command_comment"' in state

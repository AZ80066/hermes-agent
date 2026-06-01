"""Generic configured Telegram route seam tests."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform, PlatformConfig


def _adapter(extra):
    from gateway.platforms.telegram import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    adapter.config = PlatformConfig(enabled=True, token="***", extra=extra)
    adapter._bot = MagicMock()
    adapter._reply_to_mode = "first"
    adapter._forum_command_registered = set()
    adapter._forum_lock = None
    adapter._link_preview_kwargs = lambda: {}
    adapter._thread_kwargs_for_send = lambda *args, **kwargs: {"message_thread_id": int(args[1])}
    adapter._send_message_with_thread_fallback = AsyncMock(return_value=SimpleNamespace(message_id=99))
    return adapter


def test_configured_command_routes_contribute_menu_commands():
    adapter = _adapter({
        "telegram_routes": {
            "command_routes": {
                "kopa_status": {"description": "Kopa status", "handler": {"type": "subprocess", "argv": ["python3", "adapter.py"]}},
                "hidden": {"description": "Hidden", "show_in_menu": False, "handler": {"type": "subprocess", "argv": ["python3", "adapter.py"]}},
            }
        }
    })

    assert adapter._configured_telegram_menu_commands() == [("kopa_status", "Kopa status")]


def test_configured_command_route_matches_names_and_aliases():
    adapter = _adapter({
        "telegram_routes": {
            "command_routes": {
                "kopa_status": {"aliases": ["ks"], "handler": {"type": "subprocess", "argv": ["python3", "adapter.py"]}},
            }
        }
    })

    matched = adapter._configured_command_route_for("/ks@SomeBot")
    assert matched is not None
    assert matched[0] == "ks"


@pytest.mark.asyncio
async def test_configured_command_route_invokes_subprocess_and_sends_inline_keyboard(tmp_path: Path):
    script = tmp_path / "route.py"
    script.write_text(
        "import json, sys\n"
        "envelope=json.loads(sys.stdin.read())\n"
        "print(json.dumps({'text':'handled '+envelope['command'], 'reply_markup': {'inline_keyboard': [[{'text':'OK','callback_data':'x:ok'}]]}}))\n",
        encoding="utf-8",
    )
    adapter = _adapter({
        "telegram_routes": {
            "command_routes": {
                "kopa_status": {"handler": {"type": "subprocess", "argv": ["python3", str(script)], "timeout_seconds": 5}}
            }
        }
    })
    msg = SimpleNamespace(chat_id=-100, message_thread_id=42, message_id=7, text="/kopa_status")

    route = adapter._configured_command_route_for(msg.text)
    await adapter._send_configured_telegram_command_route(msg, route[1])

    sent = adapter._send_message_with_thread_fallback.call_args.kwargs
    assert sent["chat_id"] == -100
    assert sent["message_thread_id"] == 42
    assert sent["text"] == "handled kopa_status"
    assert sent["reply_markup"] is not None


@pytest.mark.asyncio
async def test_configured_callback_route_denies_unauthorized_user_before_handler():
    adapter = _adapter({
        "telegram_routes": {
            "callback_routes": [
                {"prefix": "kopa:", "handler": {"type": "subprocess", "argv": ["python3", "adapter.py"]}}
            ]
        }
    })
    adapter._is_callback_user_authorized = MagicMock(return_value=False)
    adapter._handle_configured_telegram_callback_route = AsyncMock()
    query = MagicMock()
    query.data = "kopa:status"
    query.from_user = SimpleNamespace(id="not-allowed", first_name="Nope")
    query.answer = AsyncMock()
    query.message = SimpleNamespace(
        chat_id=-100,
        message_thread_id=42,
        message=8,
        chat=SimpleNamespace(type="supergroup"),
        text="/kopa",
    )
    update = SimpleNamespace(callback_query=query)

    await adapter._handle_callback_query(update, SimpleNamespace())

    query.answer.assert_awaited_once_with(text="⛔ 未授权")
    adapter._handle_configured_telegram_callback_route.assert_not_called()


@pytest.mark.asyncio
async def test_configured_callback_route_invokes_subprocess_and_answers(tmp_path: Path):
    script = tmp_path / "callback.py"
    script.write_text(
        "import json, sys\n"
        "envelope=json.loads(sys.stdin.read())\n"
        "print(json.dumps({'answer_text':'done '+envelope['callback_data'], 'text':'callback card'}))\n",
        encoding="utf-8",
    )
    adapter = _adapter({
        "telegram_routes": {
            "callback_routes": [
                {"prefix": "kopa:", "handler": {"type": "subprocess", "argv": ["python3", str(script)], "timeout_seconds": 5}}
            ]
        }
    })
    route = adapter._configured_callback_route_for("kopa:status")
    query = MagicMock()
    query.answer = AsyncMock()
    query.message = SimpleNamespace(chat_id=-100, message_thread_id=42, message_id=8, text="/kopa")

    await adapter._handle_configured_telegram_callback_route(query, "kopa:status", route, query_thread_id=42)

    query.answer.assert_awaited_once_with(text="done kopa:status")
    sent = adapter._send_message_with_thread_fallback.call_args.kwargs
    assert sent["text"] == "callback card"

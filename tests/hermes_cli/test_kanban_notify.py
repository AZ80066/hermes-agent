import asyncio
import pytest

from pathlib import Path
from types import SimpleNamespace
from hermes_cli import kanban_db as kb
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def test_kopa_workflow_feed_auto_subscribe_pins_default_profile_and_general_topic(kanban_home, monkeypatch):
    """Kopa passive feed defaults to General and a single owning gateway profile."""
    import hermes_cli.kanban as kanban
    import hermes_cli.kanban_db as kb

    monkeypatch.delenv("KOPA_WORKFLOW_FEED_THREAD_ID", raising=False)
    monkeypatch.delenv("KOPA_WORKFLOW_FEED_NOTIFIER_PROFILE", raising=False)
    kb.create_board("kopa-os")
    kb.set_current_board("kopa-os")

    conn = kb.connect(board="kopa-os")
    try:
        tid = kb.create_task(conn, title="自动进入 Telegram workflow feed", assignee="kopacoder")
        assert kanban._maybe_auto_subscribe_kopa_workflow_feed(conn, tid) is True
        subs = kb.list_notify_subs(conn, tid)
    finally:
        conn.close()

    assert len(subs) == 1
    assert subs[0]["platform"] == "telegram"
    assert subs[0]["chat_id"] == "-1003874719298"
    assert subs[0]["thread_id"] == ""
    assert subs[0]["notifier_profile"] == "default"


def test_kopa_workflow_feed_does_not_auto_subscribe_noise_or_unowned_tasks(kanban_home, monkeypatch):
    """Auto feed is for real owned lifecycle tasks, not smoke/proof noise."""
    import hermes_cli.kanban as kanban
    import hermes_cli.kanban_db as kb

    monkeypatch.delenv("KOPA_WORKFLOW_FEED_INCLUDE_SMOKE", raising=False)
    monkeypatch.delenv("KOPA_WORKFLOW_FEED_ALLOW_UNASSIGNED", raising=False)
    kb.create_board("kopa-os")
    kb.set_current_board("kopa-os")

    conn = kb.connect(board="kopa-os")
    try:
        smoke_id = kb.create_task(
            conn,
            title="V37 feed smoke：单条低噪进程卡",
            body="验证 message_id proof，不代表交付。",
            assignee="kopawk",
        )
        unowned_id = kb.create_task(
            conn,
            title="V37 feed runtime adoption package",
            body="真实工作但尚未分配 owner。",
            assignee=None,
        )
        real_id = kb.create_task(
            conn,
            title="V37 feed runtime adoption package",
            body="恢复真实任务生命周期进程卡。",
            assignee="kopawk",
        )
        assert kanban._maybe_auto_subscribe_kopa_workflow_feed(conn, smoke_id) is False
        assert kanban._maybe_auto_subscribe_kopa_workflow_feed(conn, unowned_id) is False
        assert kanban._maybe_auto_subscribe_kopa_workflow_feed(conn, real_id) is True
        assert kb.list_notify_subs(conn, smoke_id) == []
        assert kb.list_notify_subs(conn, unowned_id) == []
        real_subs = kb.list_notify_subs(conn, real_id)
    finally:
        conn.close()

    assert len(real_subs) == 1


def test_kopa_workflow_feed_allows_real_tasks_that_mention_noise_terms(kanban_home, monkeypatch):
    """Noise words in a real task's acceptance notes must not suppress adoption."""
    import hermes_cli.kanban as kanban
    import hermes_cli.kanban_db as kb

    monkeypatch.delenv("KOPA_WORKFLOW_FEED_INCLUDE_SMOKE", raising=False)
    kb.create_board("kopa-os")
    kb.set_current_board("kopa-os")

    conn = kb.connect(board="kopa-os")
    try:
        real_id = kb.create_task(
            conn,
            title="V37 feed runtime adoption：真实任务生命周期卡",
            body="验证 no smoke/evidence-comment noise，不是创建 smoke/proof 任务。",
            assignee="kopawk",
        )
        assert kanban._maybe_auto_subscribe_kopa_workflow_feed(conn, real_id) is True
        subs = kb.list_notify_subs(conn, real_id)
    finally:
        conn.close()

    assert len(subs) == 1


def test_kopa_workflow_feed_blocks_body_only_explicit_proof_tasks(kanban_home, monkeypatch):
    """Explicit proof/evidence tasks stay out even when their title is neutral."""
    import hermes_cli.kanban as kanban
    import hermes_cli.kanban_db as kb

    monkeypatch.delenv("KOPA_WORKFLOW_FEED_INCLUDE_SMOKE", raising=False)
    kb.create_board("kopa-os")
    kb.set_current_board("kopa-os")

    conn = kb.connect(board="kopa-os")
    try:
        proof_id = kb.create_task(
            conn,
            title="V37 feed route verification",
            body="delivery_evidence proof only; not a real delivery task",
            assignee="kopawk",
        )
        assert kanban._maybe_auto_subscribe_kopa_workflow_feed(conn, proof_id) is False
        subs = kb.list_notify_subs(conn, proof_id)
    finally:
        conn.close()

    assert subs == []


@pytest.mark.asyncio
async def test_notifier_unsubs_after_completed_event(kanban_home):
    """
    Subscription should be removed after completed event
    """
    import hermes_cli.kanban_db as kb
    from gateway.run import GatewayRunner
    from gateway.config import Platform

    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="test task", assignee="worker1")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat1")
        kb.complete_task(conn, tid, result="completed by agent")
    finally:
        conn.close()

    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._kanban_sub_fail_counts = {}

    fake_adapter = MagicMock()

    async def _send_and_stop(chat_id, msg, metadata=None):
        runner._running = False

    fake_adapter.send = AsyncMock(side_effect=_send_and_stop)
    runner.adapters = {Platform.TELEGRAM: fake_adapter}

    _orig_sleep = asyncio.sleep

    async def _fast_sleep(_):
        await _orig_sleep(0)

    with patch("gateway.run.asyncio.sleep", side_effect=_fast_sleep):
        await asyncio.wait_for(
            runner._kanban_notifier_watcher(interval=1),
            timeout=10.0,
        )

    fake_adapter.send.assert_called_once()
    call_msg = fake_adapter.send.call_args[0][1]
    assert "completed" in call_msg

    conn = kb.connect()
    try:
        subs = kb.list_notify_subs(conn, tid)
    finally:
        conn.close()
    assert subs == [], "Subscription should be unsub after completed event"


@pytest.mark.asyncio
async def test_kopa_workflow_feed_payload_renders_readable_card(kanban_home):
    """Annotated Kopa events render as Chinese workflow cards, not raw Kanban pings."""
    import hermes_cli.kanban_db as kb
    from gateway.run import GatewayRunner
    from gateway.config import Platform

    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="V37-P8 readable feed smoke", assignee="kopawk")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat1")
        kb._append_event(
            conn,
            tid,
            "completed",
            {
                "kopa_workflow_feed": {
                    "event_type": "closeout_or_partial_closeout",
                    "summary": "runtime Kanban notifier 已渲染易读 workflow card",
                    "hierarchy": {
                        "version": "v0.3.7-alpha",
                        "milestone": "Founder-visible Workflow Progress Feed",
                        "package": "V37-P8",
                        "task": "runtime-adoption-smoke",
                    },
                    "owner": "kopawk / Workflow Keeper",
                    "next_step": "记录 message_id 并关闭 runtime adoption smoke gate",
                    "boundary": "只证明本次 runtime notifier smoke；不是完整发布或验收",
                    "claim_boundary": {
                        "version_delivered": False,
                        "deployed_live": False,
                        "founder_accepted_live_outcome": False,
                    },
                }
            },
        )
        conn.commit()
    finally:
        conn.close()

    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._kanban_sub_fail_counts = {}

    fake_adapter = MagicMock()

    async def _send_and_stop(chat_id, msg, metadata=None):
        runner._running = False

    fake_adapter.send = AsyncMock(side_effect=_send_and_stop)
    runner.adapters = {Platform.TELEGRAM: fake_adapter}

    _orig_sleep = asyncio.sleep

    async def _fast_sleep(_):
        await _orig_sleep(0)

    with patch("gateway.run.asyncio.sleep", side_effect=_fast_sleep):
        await asyncio.wait_for(
            runner._kanban_notifier_watcher(interval=1),
            timeout=10.0,
        )

    fake_adapter.send.assert_called_once()
    call_msg = fake_adapter.send.call_args[0][1]
    assert call_msg.startswith("✅ 已收口\n")
    assert "✅ runtime Kanban notifier 已渲染易读 workflow card" in call_msg
    assert "🔎 Kanban 已记录" in call_msg
    assert "📍 v0.3.7-alpha / V37-P8 / runtime-adoption-smoke" in call_msg
    assert "👤 kopawk / Workflow Keeper" in call_msg
    assert "➡️ 记录 message_id 并关闭 runtime adoption smoke gate" in call_msg
    assert all(line[:1] in "✅🔎📍👤➡⚠🧪⛔📌📥🚦🔧🤝📚" for line in call_msg.splitlines())
    assert all(len(line) <= 80 for line in call_msg.splitlines())
    assert "结论：" not in call_msg
    assert "Kanban t_" not in call_msg
    assert "deployed-live" not in call_msg


def test_kopa_board_generic_terminal_event_uses_fixed_founder_feed_template():
    """Unannotated Kopa board terminal events must not fall back to raw Kanban pings."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    task = SimpleNamespace(id="t_demo1234", title="Fix readable feed drift", assignee="kopawk")
    event = SimpleNamespace(kind="completed", task_id="t_demo1234", payload={"summary": "done"})

    card = runner._format_kopa_kanban_terminal_card(
        task=task,
        event=event,
        kind="completed",
        title=task.title,
        board_slug="kopa-os",
    )

    assert card is not None
    assert card.startswith("✅ 已收口\n")
    assert "✅ Fix readable feed drift 完成了" in card
    assert "🔎 Kanban 已记录：t_demo1234 / completed" in card
    assert "📍 kopa-os / t_demo1234" in card
    assert "👤 kopawk" in card
    assert "➡️ 下一步审查收口；别把单任务说成整版交付。" in card
    assert "⚠️ 只是任务进展，不是整版交付、上线或验收。" in card
    assert all(line[:1] in "✅🔎📍👤➡⚠🧪⛔📌📥🚦🔧🤝📚" for line in card.splitlines())
    assert all(len(line) <= 80 for line in card.splitlines())
    assert "Kanban t_demo1234 done" not in card
    assert "已上线" not in card


def test_kopa_board_claimed_and_commented_events_use_progress_feed_template():
    """Subscribed Kopa tasks must emit passive progress, not only terminal pings."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    task = SimpleNamespace(id="t_feed1234", title="恢复 Telegram workflow feed", assignee="kopacoder")

    claimed = runner._format_kopa_kanban_terminal_card(
        task=task,
        event=SimpleNamespace(kind="claimed", task_id="t_feed1234", payload={"profile": "kopacoder"}),
        kind="claimed",
        title=task.title,
        board_slug="kopa-os",
    )
    assert claimed is not None
    assert claimed.startswith("🔧 开始执行\n")
    assert "✅ 恢复 Telegram workflow feed 开始执行" in claimed
    assert "➡️ 执行中；后续进展会继续进 feed。" in claimed
    assert "Kanban t_feed1234" not in claimed

    commented = runner._format_kopa_kanban_terminal_card(
        task=task,
        event=SimpleNamespace(kind="commented", task_id="t_feed1234", payload={"summary": "已定位 notifier 只推 terminal events"}),
        kind="commented",
        title=task.title,
        board_slug="kopa-os",
    )
    assert commented is not None
    assert commented.startswith("🔎 进展更新\n")
    assert "已定位 notifier 只推 terminal events" in commented
    assert "➡️ 继续看下一次进展、阻塞或完成卡。" in commented
    assert all(line[:1] in "✅🔎📍👤➡⚠🧪⛔📌📥🚦🔧🤝📚" for line in commented.splitlines())


@pytest.mark.parametrize(
    ("kind", "payload", "expected_label", "expected_text", "expected_next"),
    [
        ("created", {}, "📥 已收到", "已进入队列", "等待分派或 worker claim。"),
        ("assigned", {"assignee": "kopawk"}, "🚦 已分派", "已分派给 kopawk", "等待对应角色开始执行。"),
        ("claim_extended", {"reason": "still running"}, "🔎 进展更新", "仍在执行", "继续执行；若超时再升级处理。"),
        ("unblocked", {"reason": "Founder approved"}, "🔎 进展更新", "已恢复推进：Founder approved", "继续执行；若再次卡住会发阻塞卡。"),
    ],
)
def test_kopa_board_lifecycle_events_use_founder_feed_template(
    kind, payload, expected_label, expected_text, expected_next
):
    """All lifecycle feed kinds promised by the notifier render as readable cards."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    task = SimpleNamespace(id="t_life1234", title="真实 workflow lifecycle", assignee="kopawk")

    card = runner._format_kopa_kanban_terminal_card(
        task=task,
        event=SimpleNamespace(kind=kind, task_id="t_life1234", payload=payload),
        kind=kind,
        title=task.title,
        board_slug="kopa-os",
    )

    assert card is not None
    assert card.startswith(f"{expected_label}\n")
    assert f"✅ 真实 workflow lifecycle {expected_text}" in card
    assert f"➡️ {expected_next}" in card
    assert "⚠️ 只是任务进展，不是整版交付、上线或验收。" in card
    assert "Kanban t_life1234" not in card
    assert all(line[:1] in "✅🔎📍👤➡⚠🧪⛔📌📥🚦🔧🤝📚" for line in card.splitlines())


@pytest.mark.asyncio
async def test_auto_subscribed_kopa_task_delivers_initial_created_event(kanban_home, monkeypatch):
    """Auto-subscribe must make the task's initial created event visible once."""
    import hermes_cli.kanban_db as kb
    from hermes_cli.kanban_workflow_feed import maybe_auto_subscribe_kopa_workflow_feed
    from gateway.run import GatewayRunner
    from gateway.config import Platform

    monkeypatch.delenv("KOPA_WORKFLOW_FEED_INCLUDE_SMOKE", raising=False)
    kb.create_board("kopa-os")
    kb.set_current_board("kopa-os")
    conn = kb.connect(board="kopa-os")
    try:
        tid = kb.create_task(conn, title="真实 lifecycle created delivery", assignee="kopawk")
        assert maybe_auto_subscribe_kopa_workflow_feed(conn, tid, board="kopa-os") is True
    finally:
        conn.close()

    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._kanban_sub_fail_counts = {}

    sent: list[str] = []

    async def _send_and_stop(chat_id, msg, metadata=None):
        sent.append(msg)
        runner._running = False

    fake_adapter = MagicMock()
    fake_adapter.send = AsyncMock(side_effect=_send_and_stop)
    runner.adapters = {Platform.TELEGRAM: fake_adapter}

    _orig_sleep = asyncio.sleep

    async def _fast_sleep(_):
        await _orig_sleep(0)

    with patch("gateway.run.asyncio.sleep", side_effect=_fast_sleep):
        await asyncio.wait_for(
            runner._kanban_notifier_watcher(interval=1),
            timeout=10.0,
        )

    assert len(sent) == 1
    assert sent[0].startswith("📥 已收到\n")
    assert "真实 lifecycle created delivery 已进入队列" in sent[0]

    conn = kb.connect(board="kopa-os")
    try:
        subs = kb.list_notify_subs(conn, tid)
    finally:
        conn.close()
    assert len(subs) == 1, "created event should not auto-unsubscribe the lifecycle feed"


@pytest.mark.asyncio
async def test_notifier_skips_own_delivery_evidence_comments(kanban_home):
    """Notifier must not turn its own delivery-evidence comments into feed spam."""
    import hermes_cli.kanban_db as kb
    from gateway.run import GatewayRunner
    from gateway.config import Platform
    kb.create_board("kopa-os")
    kb.set_current_board("kopa-os")

    conn = kb.connect(board="kopa-os")
    try:
        tid = kb.create_task(conn, title="feed loop guard", assignee="kopawk")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat1")
        created_id = kb.list_events(conn, tid)[-1].id
        kb.advance_notify_cursor(
            conn,
            task_id=tid,
            platform="telegram",
            chat_id="chat1",
            thread_id="",
            new_cursor=created_id,
        )
        kb.add_comment(conn, tid, "kanban-notifier", "kanban-notifier delivery_evidence\n{}")
        kb.add_comment(conn, tid, "kopawk", "真实进展：已定位 feed loop guard")
    finally:
        conn.close()

    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._kanban_sub_fail_counts = {}

    sent: list[str] = []

    async def _send_and_stop(chat_id, msg, metadata=None):
        sent.append(msg)
        runner._running = False

    fake_adapter = MagicMock()
    fake_adapter.send = AsyncMock(side_effect=_send_and_stop)
    runner.adapters = {Platform.TELEGRAM: fake_adapter}

    _orig_sleep = asyncio.sleep

    async def _fast_sleep(_):
        await _orig_sleep(0)

    with patch("gateway.run.asyncio.sleep", side_effect=_fast_sleep):
        await asyncio.wait_for(
            runner._kanban_notifier_watcher(interval=1),
            timeout=10.0,
        )

    assert len(sent) == 1
    assert "feed loop guard 有进展" in sent[0]
    assert "delivery_evidence" not in sent[0]


@pytest.mark.asyncio
async def test_notifier_does_not_unsub_done_task_before_completed_event_claimed(kanban_home):
    """A comment/done race must not drop the later completed event.

    The notifier claims unseen events, then reads the task row. If a task is
    completed between those two steps, the task snapshot is already ``done``
    while the claimed batch may contain only a progress comment. The
    subscription must survive so the next tick can deliver ``completed``.
    """
    import hermes_cli.kanban_db as kb
    from gateway.run import GatewayRunner
    from gateway.config import Platform

    kb.create_board("kopa-os")
    kb.set_current_board("kopa-os")
    conn = kb.connect(board="kopa-os")
    try:
        tid = kb.create_task(conn, title="comment then done race", assignee="worker1")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat1")
        created_id = kb.list_events(conn, tid)[-1].id
        kb.advance_notify_cursor(
            conn,
            task_id=tid,
            platform="telegram",
            chat_id="chat1",
            thread_id="",
            new_cursor=created_id,
        )
        task = kb.claim_task(conn, tid, ttl_seconds=300)
        assert task is not None
        claimed_id = kb.list_events(conn, tid)[-1].id
        kb.advance_notify_cursor(
            conn,
            task_id=tid,
            platform="telegram",
            chat_id="chat1",
            thread_id="",
            new_cursor=claimed_id,
        )
        kb.add_comment(conn, tid, "worker1", "progress before immediate closeout")
    finally:
        conn.close()

    original_claim = kb.claim_unseen_events_for_sub
    completed_inside_claim = False

    def _claim_then_complete(*args, **kwargs):
        nonlocal completed_inside_claim
        old_cursor, cursor, events = original_claim(*args, **kwargs)
        if not completed_inside_claim and any(ev.kind == "commented" for ev in events):
            completed_inside_claim = True
            # Complete after the notifier claimed the comment batch but before
            # it reads task.status. This reproduces the live race.
            kb.complete_task(args[0], tid, result="done", summary="done after comment claim")
        return old_cursor, cursor, events

    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._kanban_sub_fail_counts = {}

    sent: list[str] = []

    async def _send_and_stop(chat_id, msg, metadata=None):
        sent.append(msg)
        runner._running = False

    fake_adapter = MagicMock()
    fake_adapter.send = AsyncMock(side_effect=_send_and_stop)
    runner.adapters = {Platform.TELEGRAM: fake_adapter}

    _orig_sleep = asyncio.sleep

    async def _fast_sleep(_):
        await _orig_sleep(0)

    with patch("hermes_cli.kanban_db.claim_unseen_events_for_sub", side_effect=_claim_then_complete):
        with patch("gateway.run.asyncio.sleep", side_effect=_fast_sleep):
            await asyncio.wait_for(
                runner._kanban_notifier_watcher(interval=1),
                timeout=10.0,
            )

    assert completed_inside_claim is True
    assert len(sent) == 1
    assert "comment then done race 有进展" in sent[0]

    conn = kb.connect(board="kopa-os")
    try:
        subs = kb.list_notify_subs(conn, tid)
        assert len(subs) == 1, "subscription must survive until completed event is delivered"
        _, _, remaining = kb.claim_unseen_events_for_sub(
            conn,
            task_id=tid,
            platform="telegram",
            chat_id="chat1",
            thread_id="",
            kinds=("completed",),
        )
    finally:
        conn.close()
    assert [ev.kind for ev in remaining] == ["completed"]


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ["gave_up", "crashed", "timed_out"])
async def test_notifier_unsubs_after_abnormal_events(kind, kanban_home):
    """
    Event kinds gave_up / crashed / timed_out send a notification but DO
    NOT delete the subscription. The dispatcher may respawn the task and
    fire the same event kind again (e.g. a worker that crashes, gets
    reclaimed, and crashes a second time); the user must hear about the
    second event too. Subscriptions are removed only when the task hits
    a truly final status (done / archived) — see the comment on
    FEED_KINDS in gateway/run.py and PR #21398.
    """
    import hermes_cli.kanban_db as kb
    from gateway.run import GatewayRunner
    from gateway.config import Platform

    conn = kb.connect()

    try:
        tid = kb.create_task(conn, title=f"test {kind} task", assignee="worker1")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat1")
        kb._append_event(conn, tid, kind=kind)
    finally:
        conn.close()

    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._kanban_sub_fail_counts = {}

    fake_adapter = MagicMock()

    async def _send_and_stop(chat_id, msg, metadata=None):
        runner._running = False

    fake_adapter.send = AsyncMock(side_effect=_send_and_stop)
    runner.adapters = {Platform.TELEGRAM: fake_adapter}

    _orig_sleep = asyncio.sleep

    async def _fast_sleep(_):
        await _orig_sleep(0)

    with patch("gateway.run.asyncio.sleep", side_effect=_fast_sleep):
        await asyncio.wait_for(
            runner._kanban_notifier_watcher(interval=1),
            timeout=10.0,
        )

    # The user is notified about the abnormal event...
    fake_adapter.send.assert_called_once()
    assert kind.replace('_', ' ') in fake_adapter.send.call_args[0][1]

    # ...but the subscription survives so a respawn-then-same-event cycle
    # reaches the user too. The cursor (last_event_id) advanced inside
    # the same write txn as the claim, so the same event won't re-fire.
    conn = kb.connect()
    try:
        subs = kb.list_notify_subs(conn, tid)
    finally:
        conn.close()
    assert len(subs) == 1, (
        f"Subscription should survive {kind!r} so the next cycle of the "
        f"same event reaches the user; got {subs!r}"
    )
    assert int(subs[0]["last_event_id"]) >= 1, (
        "Cursor should have advanced past the delivered event "
        "(claim_unseen_events_for_sub advances atomically inside the "
        "same write txn as the read)."
    )


@pytest.mark.asyncio
async def test_notifier_second_blocked_delivers(kanban_home):
    """
    After the first blocked, should receive second blocked notification.
    """
    import hermes_cli.kanban_db as kb
    from gateway.run import GatewayRunner
    from gateway.config import Platform

    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._kanban_sub_fail_counts = {}

    delivered_msgs: list[str] = []

    async def _capture_send(chat_id, msg, metadata=None):
        delivered_msgs.append(msg)

    fake_adapter = MagicMock()
    fake_adapter.send = AsyncMock(side_effect=_capture_send)
    runner.adapters = {Platform.TELEGRAM: fake_adapter}

    _orig_sleep = asyncio.sleep
    tick_count = 0

    async def _fast_sleep(_):
        nonlocal tick_count
        await _orig_sleep(0)
        tick_count += 1
        if tick_count >= 6:
            runner._running = False

    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="test task", assignee="worker1")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat1")

        # Cycle 1: blocked
        kb.block_task(conn, tid, reason="first block")
    finally:
        conn.close()

    with patch("gateway.run.asyncio.sleep", side_effect=_fast_sleep):
        await asyncio.wait_for(
            runner._kanban_notifier_watcher(interval=1),
            timeout=10.0,
        )

    # Cycle 2: unblock → block run again
    runner._running = True
    tick_count = 0

    conn = kb.connect()
    try:
        kb.unblock_task(conn, tid)
        kb.block_task(conn, tid, reason="second block")
    finally:
        conn.close()

    with patch("gateway.run.asyncio.sleep", side_effect=_fast_sleep):
        await asyncio.wait_for(
            runner._kanban_notifier_watcher(interval=1),
            timeout=10.0,
        )

    blocked_deliveries = [m for m in delivered_msgs if "blocked" in m]
    assert "second block" not in blocked_deliveries[0]
    assert "second block" in blocked_deliveries[1]
    assert len(blocked_deliveries) == 2, (
        f"Should receive 2 blocked notification, but only get {len(blocked_deliveries)} count\n"
        f"Message {delivered_msgs}"
    )


# ---------------------------------------------------------------------------
# Regression: gateway watchers must not double-init the kanban DB.
#
# Both the notifier watcher (`_kanban_notifier_watcher`) and the dispatcher
# tick (`_tick_once_for_board`) used to call `_kb.connect(board=slug)`
# immediately followed by `_kb.init_db(board=slug)`. Since `connect()`
# already runs the schema + idempotent migration on first open per process,
# the explicit `init_db()` was redundant — and worse, `init_db()`
# deliberately busts the per-process cache and re-runs the migration on a
# *second* connection, which races the first.  On legacy DBs this surfaced
# as `duplicate column name: <col>` (now tolerated by
# `_add_column_if_missing`) and intermittent `database is locked` errors
# (issue #21378).
#
# The fix removes the `init_db()` calls in both watchers; this regression
# test pins that behaviour so we don't reintroduce them.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notifier_does_not_call_init_db(kanban_home):
    """Notifier watcher path must not invoke `_kb.init_db` (issue #21378)."""
    import hermes_cli.kanban_db as kb
    from gateway.run import GatewayRunner
    from gateway.config import Platform

    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._kanban_sub_fail_counts = {}

    fake_adapter = MagicMock()
    fake_adapter.send = AsyncMock()
    runner.adapters = {Platform.TELEGRAM: fake_adapter}

    _orig_sleep = asyncio.sleep
    tick_count = 0

    async def _fast_sleep(_):
        nonlocal tick_count
        await _orig_sleep(0)
        tick_count += 1
        if tick_count >= 3:
            runner._running = False

    init_db_calls: list[object] = []
    real_init_db = kb.init_db

    def _spy_init_db(*args, **kwargs):
        init_db_calls.append((args, kwargs))
        return real_init_db(*args, **kwargs)

    with patch("gateway.run.asyncio.sleep", side_effect=_fast_sleep), \
         patch("hermes_cli.kanban_db.init_db", side_effect=_spy_init_db):
        await asyncio.wait_for(
            runner._kanban_notifier_watcher(interval=1),
            timeout=10.0,
        )

    assert init_db_calls == [], (
        "_kanban_notifier_watcher must not call init_db on every tick — "
        "connect() handles first-run schema init. "
        "Reintroducing init_db revives issue #21378. "
        f"Got {len(init_db_calls)} call(s): {init_db_calls}"
    )


def test_dispatcher_tick_does_not_call_init_db(kanban_home, monkeypatch):
    """`_tick_once_for_board` must not invoke `_kb.init_db` (issue #21378).

    `connect()` already runs the schema + idempotent migration on first open
    per process. The explicit `init_db()` call was redundant and triggered a
    second migration on a second connection that raced the first.
    """
    import hermes_cli.kanban_db as kb
    from gateway.run import GatewayRunner
    from unittest.mock import patch

    runner = object.__new__(GatewayRunner)

    init_db_calls: list[object] = []
    real_init_db = kb.init_db

    def _spy_init_db(*args, **kwargs):
        init_db_calls.append((args, kwargs))
        return real_init_db(*args, **kwargs)

    # The dispatcher watcher's tick lives as a local closure inside
    # `_kanban_dispatcher_watcher`. Read the source and assert the
    # specific patterns that would reintroduce the bug are absent.
    import inspect
    src = inspect.getsource(GatewayRunner._kanban_dispatcher_watcher)
    assert "_kb.init_db(board=slug)" not in src, (
        "_kanban_dispatcher_watcher must not call _kb.init_db(board=slug) — "
        "see issue #21378. Use connect() alone; it runs migrations on first "
        "open per process."
    )

    notifier_src = inspect.getsource(GatewayRunner._kanban_notifier_watcher)
    assert "_kb.init_db(board=slug)" not in notifier_src, (
        "_kanban_notifier_watcher must not call _kb.init_db(board=slug) — "
        "see issue #21378."
    )


@pytest.mark.asyncio
async def test_notifier_skips_subscription_owned_by_other_profile(kanban_home):
    """Each gateway keeps its watcher on, but only the subscribing profile claims."""
    import hermes_cli.kanban_db as kb
    from gateway.run import GatewayRunner
    from gateway.config import Platform

    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="owned task", assignee="backend-engineer")
        kb.add_notify_sub(
            conn,
            task_id=tid,
            platform="telegram",
            chat_id="chat1",
            notifier_profile="default",
        )
        kb.complete_task(conn, tid, result="done")
    finally:
        conn.close()

    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._kanban_sub_fail_counts = {}
    runner._kanban_notifier_profile = "business-partner"

    fake_adapter = MagicMock()
    fake_adapter.send = AsyncMock()
    runner.adapters = {Platform.TELEGRAM: fake_adapter}

    _orig_sleep = asyncio.sleep
    tick_count = 0

    async def _fast_sleep(_):
        nonlocal tick_count
        await _orig_sleep(0)
        tick_count += 1
        if tick_count >= 3:
            runner._running = False

    with patch("gateway.run.asyncio.sleep", side_effect=_fast_sleep):
        await asyncio.wait_for(
            runner._kanban_notifier_watcher(interval=1),
            timeout=10.0,
        )

    fake_adapter.send.assert_not_called()
    conn = kb.connect()
    try:
        subs = kb.list_notify_subs(conn, tid)
    finally:
        conn.close()
    assert len(subs) == 1
    assert int(subs[0]["last_event_id"]) == 0, "wrong profile must not claim the event"


@pytest.mark.asyncio
async def test_notifier_delivers_subscription_owned_by_current_profile(kanban_home):
    """The gateway for the profile that created/subscribed the task reports it."""
    import hermes_cli.kanban_db as kb
    from gateway.run import GatewayRunner
    from gateway.config import Platform

    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="owned task", assignee="backend-engineer")
        kb.add_notify_sub(
            conn,
            task_id=tid,
            platform="telegram",
            chat_id="chat1",
            notifier_profile="default",
        )
        kb.complete_task(conn, tid, result="done")
    finally:
        conn.close()

    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._kanban_sub_fail_counts = {}
    runner._kanban_notifier_profile = "default"

    fake_adapter = MagicMock()

    async def _send_and_stop(chat_id, msg, metadata=None):
        runner._running = False

    fake_adapter.send = AsyncMock(side_effect=_send_and_stop)
    runner.adapters = {Platform.TELEGRAM: fake_adapter}

    _orig_sleep = asyncio.sleep

    async def _fast_sleep(_):
        await _orig_sleep(0)

    with patch("gateway.run.asyncio.sleep", side_effect=_fast_sleep):
        await asyncio.wait_for(
            runner._kanban_notifier_watcher(interval=1),
            timeout=10.0,
        )

    fake_adapter.send.assert_called_once()
    conn = kb.connect()
    try:
        subs = kb.list_notify_subs(conn, tid)
    finally:
        conn.close()
    assert subs == []


@pytest.mark.asyncio
async def test_gateway_create_autosubscribes_on_explicit_board(kanban_home):
    """`/kanban --board <slug> create ...` must subscribe on that board.

    The gateway handler currently auto-subscribes after `/kanban create`,
    but the create detection must still work when the shared `--board`
    flag appears before the subcommand, and the subscription must land in
    that board's DB rather than the ambient/default board.
    """
    from gateway.run import GatewayRunner
    from gateway.config import Platform

    kb.create_board("projx")

    runner = object.__new__(GatewayRunner)
    source = SimpleNamespace(
        platform=Platform.TELEGRAM,
        chat_id="chat1",
        thread_id="th1",
        user_id="u1",
    )
    event = SimpleNamespace(
        text='/kanban --board projx create "hello" --assignee alice',
        source=source,
    )

    out = await GatewayRunner._handle_kanban_command(runner, event)

    assert "subscribed" in out.lower()

    conn = kb.connect(board="projx")
    try:
        subs = kb.list_notify_subs(conn)
        tasks = kb.list_tasks(conn)
    finally:
        conn.close()

    assert [t.title for t in tasks] == ["hello"]
    assert len(subs) == 1
    assert subs[0]["chat_id"] == "chat1"
    assert subs[0]["thread_id"] == "th1"

    conn = kb.connect(board="default")
    try:
        assert kb.list_notify_subs(conn) == []
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_notifier_uploads_artifacts_on_completion(kanban_home, tmp_path):
    """When a completed event carries ``artifacts`` in its payload, the
    notifier uploads each file to the subscribed chat as a native
    attachment. Images batch through send_multiple_images; documents
    route through send_document. See the artifacts wiring in
    gateway/run.py._deliver_kanban_artifacts.
    """
    import hermes_cli.kanban_db as kb
    from gateway.run import GatewayRunner
    from gateway.config import Platform
    from tools import kanban_tools as kt

    # Materialize real files so os.path.isfile passes inside the helper.
    chart_path = tmp_path / "q3-revenue.png"
    chart_path.write_bytes(b"PNG-fake-bytes")
    report_path = tmp_path / "report.pdf"
    report_path.write_bytes(b"%PDF-fake")

    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="render q3 chart", assignee="worker1")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat1")
    finally:
        conn.close()

    # Use the production handler so we exercise the full path: tool args
    # → metadata.artifacts → event payload promotion.
    import os
    os.environ["HERMES_KANBAN_TASK"] = tid
    try:
        out = kt._handle_complete({
            "summary": "rendered the chart",
            "artifacts": [str(chart_path), str(report_path)],
        })
    finally:
        os.environ.pop("HERMES_KANBAN_TASK", None)
    import json as _json
    assert _json.loads(out)["ok"] is True

    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._kanban_sub_fail_counts = {}

    fake_adapter = MagicMock()
    fake_adapter.name = "telegram"

    sends: list = []
    images_uploaded: list = []
    documents_uploaded: list = []

    async def _send(chat_id, msg, metadata=None):
        sends.append((chat_id, msg))
        runner._running = False

    async def _send_images(chat_id, images, metadata=None, **_kw):
        images_uploaded.extend(p for p, _ in images)

    async def _send_document(chat_id, file_path, metadata=None, **_kw):
        documents_uploaded.append(file_path)

    fake_adapter.send = AsyncMock(side_effect=_send)
    fake_adapter.send_multiple_images = AsyncMock(side_effect=_send_images)
    fake_adapter.send_document = AsyncMock(side_effect=_send_document)
    # extract_local_files is used internally for legacy path fallback;
    # the real BasePlatformAdapter implementation lives there, so wire it.
    from gateway.platforms.base import BasePlatformAdapter
    fake_adapter.extract_local_files = BasePlatformAdapter.extract_local_files

    runner.adapters = {Platform.TELEGRAM: fake_adapter}

    _orig_sleep = asyncio.sleep

    async def _fast_sleep(_):
        await _orig_sleep(0)

    with patch("gateway.run.asyncio.sleep", side_effect=_fast_sleep):
        await asyncio.wait_for(
            runner._kanban_notifier_watcher(interval=1),
            timeout=10.0,
        )

    # The text completion notification fired.
    assert len(sends) == 1
    # The PNG rode the image-batch path.
    assert any("q3-revenue.png" in p for p in images_uploaded), images_uploaded
    # The PDF rode the document path.
    assert any("report.pdf" in p for p in documents_uploaded), documents_uploaded


@pytest.mark.asyncio
async def test_notifier_artifact_delivery_skips_missing_files(kanban_home, tmp_path):
    """Missing artifact paths are silently skipped — they may have been
    referenced by name only. The notifier must not crash and must still
    deliver any artifacts that do exist."""
    import hermes_cli.kanban_db as kb
    from gateway.run import GatewayRunner
    from gateway.config import Platform
    from tools import kanban_tools as kt

    real_pdf = tmp_path / "real.pdf"
    real_pdf.write_bytes(b"%PDF-fake")

    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="t", assignee="worker1")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat1")
    finally:
        conn.close()

    import os
    os.environ["HERMES_KANBAN_TASK"] = tid
    try:
        kt._handle_complete({
            "summary": "one real, one ghost",
            "artifacts": [str(real_pdf), "/tmp/definitely-does-not-exist.pdf"],
        })
    finally:
        os.environ.pop("HERMES_KANBAN_TASK", None)

    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._kanban_sub_fail_counts = {}

    fake_adapter = MagicMock()
    fake_adapter.name = "telegram"

    documents_uploaded: list = []

    async def _send(chat_id, msg, metadata=None):
        runner._running = False

    async def _send_document(chat_id, file_path, metadata=None, **_kw):
        documents_uploaded.append(file_path)

    fake_adapter.send = AsyncMock(side_effect=_send)
    fake_adapter.send_document = AsyncMock(side_effect=_send_document)
    fake_adapter.send_multiple_images = AsyncMock()
    from gateway.platforms.base import BasePlatformAdapter
    fake_adapter.extract_local_files = BasePlatformAdapter.extract_local_files

    runner.adapters = {Platform.TELEGRAM: fake_adapter}

    _orig_sleep = asyncio.sleep

    async def _fast_sleep(_):
        await _orig_sleep(0)

    with patch("gateway.run.asyncio.sleep", side_effect=_fast_sleep):
        await asyncio.wait_for(
            runner._kanban_notifier_watcher(interval=1),
            timeout=10.0,
        )

    # Only the real file was uploaded.
    assert len(documents_uploaded) == 1
    assert "real.pdf" in documents_uploaded[0]

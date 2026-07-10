from __future__ import annotations

import pytest


class Bridge:
    def __init__(self, messages):
        self._messages = list(messages)
    def get_updates(self, offset=0, timeout=1):
        return [{"update_id": offset + idx, "message": msg} for idx, msg in enumerate(self._messages)]
    def broadcast(self, _payload):
        pass

class Ctx:
    def __init__(self, state):
        self.state = dict(state)
        self.sent = []
        self.consciousness = None
        self.kill_workers = None
    def load_state(self):
        return dict(self.state)
    def save_state(self, state):
        self.state = dict(state)
    def send_with_budget(self, chat_id, text, **_kwargs):
        self.sent.append((chat_id, text))


@pytest.mark.parametrize(
    "text",
    [
        "хочу получать котомемы",
        "Подпишите меня на мемы с котами",
        "Присылайте кошачьи мемы",
        "/cats_subscribe",
    ],
)
def test_cat_meme_subscribe_intents(text):
    import server

    assert server._cat_meme_subscription_action(text) == "subscribe"


@pytest.mark.parametrize(
    "text",
    [
        "не хочу получать котомемы",
        "Перестань присылать мемы про котов",
        "Отпиши меня от кошачьих мемов",
        "/cats_unsubscribe",
    ],
)
def test_cat_meme_unsubscribe_intents(text):
    import server

    assert server._cat_meme_subscription_action(text) == "unsubscribe"


@pytest.mark.parametrize(
    "text",
    [
        "хочу получать дайджест",
        "Подпишите меня на AI дайджест",
        "присылайте новостной дайджест",
        "/digest_subscribe",
    ],
)
def test_digest_subscribe_intents(text):
    import server

    assert server._digest_subscription_action(text) == "subscribe"


@pytest.mark.parametrize(
    "text",
    [
        "не хочу получать дайджест",
        "Перестань присылать AI дайджест",
        "Отпиши меня от дайджеста",
        "/digest_unsubscribe",
    ],
)
def test_digest_unsubscribe_intents(text):
    import server

    assert server._digest_subscription_action(text) == "unsubscribe"


@pytest.mark.parametrize(
    "text",
    [
        "люблю котомемы",
        "хочу получать новости",
        "покажи одного кота",
    ],
)
def test_cat_meme_non_action_messages_do_not_change_subscription(text):
    import server

    assert server._cat_meme_subscription_action(text) is None


@pytest.mark.parametrize(
    "text",
    ["/subscriptions", "/subscription", "подписки", "Управление подписками"],
)
def test_subscription_menu_intents(text):
    import server

    assert server._is_subscription_menu_request(text) is True


def test_external_user_can_open_subscription_menu_without_becoming_owner(monkeypatch):
    import server
    import supervisor.message_bus as message_bus

    bridge = Bridge([{
        "chat": {"id": 4242},
        "from": {"id": 77},
        "text": "/subscriptions",
        "source": "skill:telegram-bridge",
    }])
    ctx = Ctx({})
    monkeypatch.setattr(message_bus, "log_chat", lambda *args, **kwargs: None)

    server._process_bridge_updates(bridge, 0, ctx)

    assert "owner_id" not in ctx.state
    assert "owner_chat_id" not in ctx.state
    assert ctx.sent[0][0] == 4242
    assert "Menu" in ctx.sent[0][1]
    assert "/cats_subscribe" not in ctx.sent[0][1]
    assert "/digest_subscribe" not in ctx.sent[0][1]


def test_external_user_subscribes_with_sender_chat_id_without_becoming_owner(monkeypatch):
    import server
    import supervisor.message_bus as message_bus

    calls = []
    bridge = Bridge([{
        "chat": {"id": 4242},
        "from": {"id": 77},
        "text": "хочу получать котомемы",
        "source": "skill:telegram-bridge",
    }])
    ctx = Ctx({})
    monkeypatch.setattr(message_bus, "log_chat", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        server,
        "_apply_post_broadcast_subscription",
        lambda _ctx, chat_id, action: calls.append((chat_id, action)) or (True, ""),
    )

    server._process_bridge_updates(bridge, 0, ctx)

    assert calls == [(4242, "subscribe")]
    assert "owner_id" not in ctx.state
    assert "owner_chat_id" not in ctx.state
    assert ctx.sent == [(
        4242,
        "✅ Вы подписаны на рассылку котомемов. Чтобы отписаться, напишите: "
        "«не хочу получать котомемы».",
    )]


def test_external_user_subscribes_to_digest_with_sender_chat_id(monkeypatch):
    import server
    import supervisor.message_bus as message_bus

    calls = []
    bridge = Bridge([{
        "chat": {"id": 4242},
        "from": {"id": 77},
        "text": "хочу получать дайджест",
        "source": "skill:telegram-bridge",
    }])
    ctx = Ctx({})
    monkeypatch.setattr(message_bus, "log_chat", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        server,
        "_apply_research_digest_subscription",
        lambda _ctx, chat_id, action: calls.append((chat_id, action)) or (True, ""),
    )

    server._process_bridge_updates(bridge, 0, ctx)

    assert calls == [(4242, "subscribe")]
    assert "owner_id" not in ctx.state
    assert "owner_chat_id" not in ctx.state
    assert ctx.sent == [(
        4242,
        "✅ Вы подписаны на AI-дайджест. Чтобы отписаться, напишите: "
        "«не хочу получать дайджест».",
    )]


def test_subscription_helper_uses_sender_chat_id_and_restores_trust(monkeypatch):
    import json
    import server
    import ouroboros.extension_loader as extension_loader

    tool_ctx = type("ToolCtx", (), {})()
    calls = []

    class Tools:
        _ctx = tool_ctx

        def execute(self, name, args):
            calls.append((
                name,
                args,
                getattr(self._ctx, "_trusted_direct_extension_tool", ""),
            ))
            return json.dumps({"ok": True})

    class SubscriptionCtx:
        def get_chat_agent(self):
            return type("Agent", (), {"tools": Tools()})()

    tool_name = "ext_16_r_post_broadcast_unsubscribe"
    monkeypatch.setattr(extension_loader, "snapshot", lambda: {"tools": [tool_name]})

    result = server._apply_post_broadcast_subscription(
        SubscriptionCtx(),
        4242,
        "unsubscribe",
    )

    assert result == (True, "")
    assert calls == [(tool_name, {"chat_id": "4242"}, tool_name)]
    assert not hasattr(tool_ctx, "_trusted_direct_extension_tool")


def test_digest_subscription_helper_uses_research_digest_tool(monkeypatch):
    import json
    import server
    import ouroboros.extension_loader as extension_loader

    calls = []

    class Tools:
        def execute(self, name, args):
            calls.append((name, args))
            return json.dumps({"ok": True})

    class SubscriptionCtx:
        def get_chat_agent(self):
            return type("Agent", (), {"tools": Tools()})()

    tool_name = "ext_18_r_research_digest_subscribe"
    monkeypatch.setattr(extension_loader, "snapshot", lambda: {"tools": [tool_name]})

    result = server._apply_research_digest_subscription(
        SubscriptionCtx(),
        4242,
        "subscribe",
    )

    assert result == (True, "")
    assert calls == [(tool_name, {"chat_id": "4242"})]


def test_external_first_slash_binds_external_owner_without_executing(monkeypatch):
    import server
    import supervisor.message_bus as message_bus
    called = []
    bridge = Bridge([{"chat": {"id": 42}, "from": {"id": 7}, "text": "/panic", "source": "skill:telegram-bridge"}])
    ctx = Ctx({})
    monkeypatch.setattr(message_bus, "log_chat", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_execute_panic_stop", lambda *args, **kwargs: called.append(True))
    server._process_bridge_updates(bridge, 0, ctx)
    # Global owner binds for outbound routing, external owner binds for slash auth.
    assert ctx.state["owner_id"] == 7
    assert ctx.state["owner_chat_id"] == 42
    assert ctx.state["owner_external_id"] == 7
    assert ctx.state["owner_external_chat_id"] == 42
    assert ctx.state["owner_external_bound_at"]
    assert called == []
    assert ctx.sent == [(42, "✅ Owner chat registered. Send the command again to execute it.")]

def test_external_non_owner_slash_is_ignored(monkeypatch):
    import server
    import supervisor.message_bus as message_bus
    called = []
    bridge = Bridge([{"chat": {"id": 99}, "from": {"id": 8}, "text": "/panic", "source": "skill:telegram-bridge"}])
    # An external owner is already bound to a different chat.
    ctx = Ctx({"owner_id": 7, "owner_chat_id": 42, "owner_external_id": 7, "owner_external_chat_id": 42})
    monkeypatch.setattr(message_bus, "log_chat", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_execute_panic_stop", lambda *args, **kwargs: called.append(True))
    server._process_bridge_updates(bridge, 0, ctx)
    assert called == []
    assert ctx.sent == [(99, "⚠️ Command ignored: this transport is not the bound owner chat.")]

def test_desktop_web_owner_does_not_lock_out_telegram(monkeypatch):
    # Regression: on desktop the web UI binds owner=1/1 first; a real Telegram
    # owner must still be able to register (TOFU) and then execute slash commands.
    import server
    import supervisor.message_bus as message_bus
    called = []
    ctx = Ctx({"owner_id": 1, "owner_chat_id": 1})
    monkeypatch.setattr(message_bus, "log_chat", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_execute_panic_stop", lambda *args, **kwargs: called.append(True))
    # First Telegram slash binds the external owner and asks for a resend.
    server._process_bridge_updates(
        Bridge([{"chat": {"id": 42}, "from": {"id": 7}, "text": "/panic", "source": "skill:telegram-bridge"}]),
        0, ctx,
    )
    assert called == []
    assert ctx.state["owner_id"] == 1 and ctx.state["owner_chat_id"] == 1
    assert ctx.state["owner_external_id"] == 7 and ctx.state["owner_external_chat_id"] == 42
    assert ctx.sent[-1] == (42, "✅ Owner chat registered. Send the command again to execute it.")
    # Resend from the bound external owner now executes.
    server._process_bridge_updates(
        Bridge([{"chat": {"id": 42}, "from": {"id": 7}, "text": "/panic", "source": "skill:telegram-bridge"}]),
        0, ctx,
    )
    assert called == [True]

def test_external_negative_id_cannot_bind_or_execute(monkeypatch):
    # Negative (A2A/synthetic) ids fail the chat_id>0 and user_id>0 gate, so they
    # can neither bind the external owner nor execute a slash command.
    import server
    import supervisor.message_bus as message_bus
    called = []
    bridge = Bridge([{"chat": {"id": -1001}, "from": {"id": -1001}, "text": "/panic", "source": "skill:a2a"}])
    ctx = Ctx({})
    monkeypatch.setattr(message_bus, "log_chat", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_execute_panic_stop", lambda *args, **kwargs: called.append(True))
    server._process_bridge_updates(bridge, 0, ctx)
    assert called == []
    assert "owner_external_id" not in ctx.state
    assert ctx.sent == [(-1001, "⚠️ Command ignored: this transport did not provide owner identity.")]

def test_external_zero_identity_cannot_bind_owner_or_execute_on_retry(monkeypatch):
    import server
    import supervisor.message_bus as message_bus
    from supervisor.message_bus import LocalChatBridge
    called = []
    bridge = LocalChatBridge()
    bridge.enqueue_local_message("/panic", chat_id=0, user_id=0, source="skill:bridge")
    bridge.enqueue_local_message("/panic", chat_id=0, user_id=0, source="skill:bridge")
    ctx = Ctx({})
    monkeypatch.setattr(message_bus, "log_chat", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_execute_panic_stop", lambda *args, **kwargs: called.append(True))
    server._process_bridge_updates(bridge, 0, ctx)
    server._process_bridge_updates(bridge, 1, ctx)
    assert called == []
    assert "owner_id" not in ctx.state
    assert "owner_external_id" not in ctx.state
    assert ctx.sent == [(0, "⚠️ Command ignored: this transport did not provide owner identity."), (0, "⚠️ Command ignored: this transport did not provide owner identity.")]

def test_external_review_rejected_before_queue_when_model_unavailable(monkeypatch):
    import server
    import ouroboros.deep_self_review as deep_self_review
    import supervisor.message_bus as message_bus
    bridge = Bridge([{"chat": {"id": 42}, "from": {"id": 7}, "text": "/review", "source": "skill:telegram-bridge"}])
    ctx = Ctx({})
    monkeypatch.setattr(message_bus, "log_chat", lambda *args, **kwargs: None)
    monkeypatch.setattr(deep_self_review, "is_review_available", lambda: (False, None))
    server._process_bridge_updates(bridge, 0, ctx)
    assert ctx.sent == [
        (42, "❌ Deep self-review unavailable: configure OUROBOROS_MODEL_DEEP_SELF_REVIEW and the matching provider API key.")
    ]

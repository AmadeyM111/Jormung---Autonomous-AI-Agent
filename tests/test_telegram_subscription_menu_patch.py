from __future__ import annotations

from ouroboros.telegram_subscription_menu_patch import (
    MARKER,
    MAX_REVIEW_FILE_BYTES,
    patch_plugin,
)


def test_subscription_menu_patch_adds_public_self_service_buttons(tmp_path):
    plugin = tmp_path / "plugin.py"
    plugin.write_text(
        '''
def _build_menu_keyboard(command_mode, lang="en"):
    return "", []

async def poll():
                await client.call("setMyCommands", data={"commands": json.dumps([])})
                    if str(_inbound_chat) != pinned_chat:
                        if _cb:
                            try:
                                await client.answer_callback_query(
                                    str(_cb.get("id") or ""),
                                    text=_LOCALIZED_TEXTS[lang]["not_authorized"],
                                )
                            except Exception:
                                pass
                            continue
                    if callback_query:
                        if not pinned_chat or str(cb_chat_id) != pinned_chat:
                            await client.answer_callback_query(cb_id, text=_LOCALIZED_TEXTS[lang]["not_authorized"])
                            continue

                        # --- Dynamic Tab Navigation (Category 1) ---
                    if is_start_cmd:
                        await client.send_message(
                            chat_id,
                            "Напишите сообщение обычным текстом. Команды управления доступны только владельцу.",
                        )
                        continue
                    # Handle /menu command locally
''',
        encoding="utf-8",
    )

    result = patch_plugin(plugin)
    patched = plugin.read_text(encoding="utf-8")

    assert result["ok"] is True
    assert MARKER in patched
    assert "s:c1" in patched
    assert "s:c0" in patched
    assert "s:d1" in patched
    assert "s:d0" in patched
    assert "sub:cats:on" in patched
    assert "sub:digest:on" in patched
    assert "else 'digest'" in patched
    assert "_subscription_command_from_callback" in patched
    assert "return _build_subscription_keyboard()" in patched
    assert "OUROBOROS_SUB_ROOT_ONLY" in patched
    assert 'text="Недоступно"' not in patched
    assert 'text="Подписки"' in patched
    assert "is_subscriptions_cmd" in patched
    assert patch_plugin(plugin)["changed"] is False
    assert len(patched.encode("utf-8")) <= MAX_REVIEW_FILE_BYTES

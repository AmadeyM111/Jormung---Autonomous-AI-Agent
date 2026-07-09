"""Patch the marketplace Telegram bridge with a public subscription menu."""

from __future__ import annotations

import pathlib
from typing import Any, Dict


MARKER = "OUROBOROS_TELEGRAM_SUBSCRIPTION_MENU"


def patch_plugin(path: pathlib.Path | str) -> Dict[str, Any]:
    plugin = pathlib.Path(path)
    try:
        text = plugin.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"ok": False, "error": f"Telegram bridge plugin not found: {plugin}"}
    if MARKER in text:
        return {"ok": True, "changed": False, "path": str(plugin)}

    original = text
    helper = f'''
# {MARKER}
def _build_subscription_keyboard() -> tuple[str, list[list[dict]]]:
    return (
        "Управление подписками",
        [
            [{{"text": "Подписаться на котомемы", "callback_data": "subscription:cats:on"}}],
            [{{"text": "Отписаться от котомемов", "callback_data": "subscription:cats:off"}}],
        ],
    )


'''
    text = text.replace("def _build_menu_keyboard(", helper + "def _build_menu_keyboard(", 1)
    text = text.replace(
        'await client.call("setMyCommands", data={"commands": json.dumps([])})',
        'await client.call("setMyCommands", data={"commands": json.dumps(['
        '{"command": "subscriptions", "description": "Управление подписками"}'
        '])})',
        1,
    )
    text = text.replace(
        """                        if _cb:
                            try:
                                await client.answer_callback_query(
                                    str(_cb.get("id") or ""),
                                    text=_LOCALIZED_TEXTS[lang]["not_authorized"],
                                )
                            except Exception:
                                pass
                            continue
""",
        """                        if _cb and str(_cb.get("data") or "") not in {
                            "subscription:cats:on", "subscription:cats:off"
                        }:
                            try:
                                await client.answer_callback_query(
                                    str(_cb.get("id") or ""),
                                    text=_LOCALIZED_TEXTS[lang]["not_authorized"],
                                )
                            except Exception:
                                pass
                            continue
""",
        1,
    )
    text = text.replace(
        """                        if not pinned_chat or str(cb_chat_id) != pinned_chat:
                            await client.answer_callback_query(cb_id, text=_LOCALIZED_TEXTS[lang]["not_authorized"])
                            continue

                        # --- Dynamic Tab Navigation (Category 1) ---
""",
        """                        if (
                            (not pinned_chat or str(cb_chat_id) != pinned_chat)
                            and cb_data not in {"subscription:cats:on", "subscription:cats:off"}
                        ):
                            await client.answer_callback_query(cb_id, text=_LOCALIZED_TEXTS[lang]["not_authorized"])
                            continue

                        if cb_data in {"subscription:cats:on", "subscription:cats:off"}:
                            command = "/cats_subscribe" if cb_data.endswith(":on") else "/cats_unsubscribe"
                            sender_name = _extract_sender_label(cb_sender, cb_chat_id)
                            sender_label = f"Telegram ({sender_name})"
                            await client.answer_callback_query(cb_id, text="Обрабатываю")
                            await _inject(api, {
                                "text": command,
                                "chat_id": cb_chat_id,
                                "user_id": int(cb_sender.get("id") or cb_chat_id or 1),
                                "source": "telegram-bridge",
                                "sender_label": sender_label,
                                "transport": {
                                    "kind": "telegram",
                                    "conversation_id": str(cb_chat_id),
                                    "sender_label": sender_label,
                                },
                                "image_base64": "",
                                "image_mime": "",
                                "image_caption": "",
                            })
                            continue

                        # --- Dynamic Tab Navigation (Category 1) ---
""",
        1,
    )
    text = text.replace(
        """                    if is_start_cmd:
                        await client.send_message(
                            chat_id,
                            "Напишите сообщение обычным текстом. Команды управления доступны только владельцу.",
                        )
                        continue
""",
        """                    if is_start_cmd:
                        header, keyboard = _build_subscription_keyboard()
                        await client.send_message_with_inline_keyboard(chat_id, header, keyboard)
                        continue
""",
        1,
    )
    text = text.replace(
        """                    # Handle /menu command locally""",
        """                    is_subscriptions_cmd = (
                        cleaned_text == "/subscriptions"
                        or cleaned_text.startswith("/subscriptions ")
                        or cleaned_text.startswith("/subscriptions@")
                    )
                    if is_subscriptions_cmd:
                        header, keyboard = _build_subscription_keyboard()
                        await client.send_message_with_inline_keyboard(chat_id, header, keyboard)
                        continue

                    # Handle /menu command locally""",
        1,
    )

    if text == original or MARKER not in text:
        return {"ok": False, "error": "Telegram bridge did not match subscription menu patch"}
    plugin.write_text(text, encoding="utf-8")
    return {"ok": True, "changed": True, "path": str(plugin)}

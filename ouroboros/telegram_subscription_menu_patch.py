"""Patch the marketplace Telegram bridge with a public subscription menu."""

from __future__ import annotations

import pathlib
import re
from typing import Any, Dict


MARKER = "OUROBOROS_TELEGRAM_SUBSCRIPTION_MENU"
MAX_REVIEW_FILE_BYTES = 65_536


def patch_plugin(path: pathlib.Path | str) -> Dict[str, Any]:
    plugin = pathlib.Path(path)
    try:
        text = plugin.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"ok": False, "error": f"Telegram bridge plugin not found: {plugin}"}
    if MARKER in text and "s:d1" in text and "else 'digest'" in text:
        return {"ok": True, "changed": False, "path": str(plugin)}
    original = text
    helper = f'''
# {MARKER}
_SUB_CB = {{"s:c1", "s:c0", "s:d1", "s:d0"}}
def _build_subscription_keyboard():
    return "Подписки", [
        [{{"text": "Коты +", "callback_data": "s:c1"}}, {{"text": "Коты -", "callback_data": "s:c0"}}],
        [{{"text": "Дайджест +", "callback_data": "s:d1"}}, {{"text": "Дайджест -", "callback_data": "s:d0"}}],
    ]


'''
    if MARKER in text:
        text = re.sub(
            rf"# {MARKER}.*?\n\ndef _build_menu_keyboard\(",
            helper + "def _build_menu_keyboard(",
            text,
            count=1,
            flags=re.S,
        )
        text = text.replace('str(_cb.get("data") or "") not in {\n                            "sub:cats:on", "sub:cats:off"\n                        }', 'str(_cb.get("data") or "") not in _SUB_CB')
        text = text.replace('cb_data not in {"sub:cats:on", "sub:cats:off"}', "cb_data not in _SUB_CB")
        text = text.replace('if cb_data in {"sub:cats:on", "sub:cats:off"}:', "if cb_data in _SUB_CB:")
        text = text.replace('command = "/cats_subscribe" if cb_data.endswith(":on") else "/cats_unsubscribe"', 'command = f"/{\'cats\' if cb_data[2] == \'c\' else \'digest\'}_{\'subscribe\' if cb_data.endswith(\'1\') else \'unsubscribe\'}"')
    else:
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
        """                        if _cb and str(_cb.get("data") or "") not in _SUB_CB:
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
                            and cb_data not in _SUB_CB
                        ):
                            await client.answer_callback_query(cb_id, text=_LOCALIZED_TEXTS[lang]["not_authorized"])
                            continue

                        if cb_data in _SUB_CB:
                            command = f"/{'cats' if cb_data[2] == 'c' else 'digest'}_{'subscribe' if cb_data.endswith('1') else 'unsubscribe'}"
                            await client.answer_callback_query(cb_id, text="Обрабатываю")
                            await _inject(api, {
                                "text": command,
                                "chat_id": cb_chat_id,
                                "user_id": int(cb_sender.get("id") or cb_chat_id or 1),
                                "source": "telegram-bridge",
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
    original_size_bytes = len(original.encode("utf-8"))
    size_bytes = len(text.encode("utf-8"))
    if original_size_bytes <= MAX_REVIEW_FILE_BYTES and size_bytes > MAX_REVIEW_FILE_BYTES:
        return {
            "ok": False,
            "error": f"subscription menu would exceed review file limit: {size_bytes} bytes",
        }
    plugin.write_text(text, encoding="utf-8")
    return {"ok": True, "changed": True, "path": str(plugin)}

"""Хранилище истории диалогов для ветки QA.

Хранит последние N сообщений пользователя + ответы агента в JSON-файлах
по одному на каждый chat_id. Не требует БД, не зависит от LangGraph checkpointer.

Структура файла history/{chat_id}.json:
[
  {"role": "user", "content": "..."},
  {"role": "assistant", "content": "..."},
  ...
]

Хранится максимум MAX_HISTORY_PAIRS пар user+assistant (по умолчанию 3 = 6 записей).
"""

import os
import json
import logging

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
HISTORY_DIR = os.path.join(BASE_DIR, "history")

MAX_HISTORY_PAIRS = 3

os.makedirs(HISTORY_DIR, exist_ok=True)


def _history_path(chat_id: int) -> str:
    return os.path.join(HISTORY_DIR, f"{chat_id}.json")


def load_history(chat_id: int) -> list[dict]:
    """Загружает историю диалога для chat_id. Возвращает список записей."""
    path = _history_path(chat_id)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_exchange(chat_id: int, user_msg: str, assistant_msg: str) -> None:
    """Добавляет пару user+assistant в историю, обрезая старые.

    Оставляет последние MAX_HISTORY_PAIRS пар (6 записей).
    """
    history = load_history(chat_id)
    history.append({"role": "user", "content": user_msg})
    history.append({"role": "assistant", "content": assistant_msg})

    max_records = MAX_HISTORY_PAIRS * 2
    if len(history) > max_records:
        history = history[-max_records:]

    path = _history_path(chat_id)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning("Failed to save history for chat %s: %s", chat_id, e)


def format_history_for_prompt(chat_id: int) -> str:
    """Форматирует историю в текст для системного промпта qa_pro.

    Возвращает строку вида:
    --- История диалога (последние сообщения) ---
    [User]: ...
    [Assistant]: ...
    """
    history = load_history(chat_id)
    if not history:
        return ""

    lines = ["--- История диалога (последние сообщения) ---"]
    for msg in history:
        role = "User" if msg["role"] == "user" else "Assistant"
        content = msg["content"][:500]
        lines.append(f"[{role}]: {content}")

    return "\n".join(lines)


def clear_history(chat_id: int) -> None:
    """Очищает историю диалога (например, по команде /clear)."""
    path = _history_path(chat_id)
    if os.path.exists(path):
        os.remove(path)

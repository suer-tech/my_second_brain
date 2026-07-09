"""TelegramNotifier — отправка живых уведомлений о работе агента в Telegram.

Показывает в реальном времени:
- какой агент/скилл активен
- какие инструменты вызываются и с какими аргументами
- результат выполнения (кратко)
- завершение этапов

Используется поверх progress callback'а: progress обновляет одно
«основное» сообщение, а notifier шлёт отдельные сообщения о вызовах.
"""

import logging
from typing import Optional, Any

from aiogram import Bot
from aiogram.enums import ParseMode

logger = logging.getLogger(__name__)

# Максимальная длина аргументов инструмента в уведомлении
_MAX_ARGS_LEN = 150
# Максимальная длина результата в уведомлении
_MAX_RESULT_LEN = 200


class TelegramNotifier:
    """Шлёт отдельные сообщения в Telegram о событиях внутри агента.

    NOTE: single-user бот — никаких блокировок и очередей.
    Все методы — fire-and-forget (try/except, без raise).
    """

    def __init__(self, bot: Bot, chat_id: int) -> None:
        self._bot = bot
        self._chat_id = chat_id

    # ─── События жизненного цикла ────────────────────────────────────────

    async def node_start(self, node_name: str, detail: str = "") -> None:
        """Запуск ноды графа (meta_orchestrator / под-агент)."""
        text = f"🧠 <b>Запуск ноды:</b> {node_name}"
        if detail:
            text += f"\n{detail}"
        await self._safe_send(text)

    async def node_end(self, node_name: str, status: str = "ok", summary: str = "") -> None:
        """Завершение ноды графа."""
        icon = "✅" if status == "ok" else "⚠️"
        text = f"{icon} <b>Нода завершена:</b> {node_name}"
        if summary:
            text += f"\n{summary}"
        await self._safe_send(text)

    async def skill_start(self, skill_name: str, task: str) -> None:
        """Скилл начал выполнение."""
        task_preview = task[:120].replace("\n", " ")
        await self._safe_send(
            f"🚀 <b>Скилл:</b> {skill_name}\n"
            f"📝 <i>{task_preview}</i>"
        )

    async def skill_end(self, skill_name: str, result_summary: str = "") -> None:
        """Скилл завершил выполнение."""
        text = f"✅ <b>Скилл завершён:</b> {skill_name}"
        if result_summary:
            text += f"\n{result_summary[:200]}"
        await self._safe_send(text)

    async def agent_start(self, agent_name: str, task: str = "") -> None:
        """Под-агент начал работу (Planner, Developer, Checker)."""
        text = f"👤 <b>Агент:</b> {agent_name}"
        if task:
            text += f"\n📌 {task[:120]}"
        await self._safe_send(text)

    async def agent_end(self, agent_name: str, result: str = "") -> None:
        """Под-агент завершил работу."""
        text = f"✓ <b>Агент завершил:</b> {agent_name}"
        if result:
            text += f"\n{result[:200]}"
        await self._safe_send(text)

    # ─── Tool calls ───────────────────────────────────────────────────────

    async def tool_call(self, tool_name: str, args: Any) -> None:
        """Вызов инструмента — показываем что и с какими аргументами."""
        args_str = _truncate(str(args), _MAX_ARGS_LEN)
        await self._safe_send(
            f"🔧 <b>Инструмент:</b> <code>{tool_name}</code>\n"
            f"📥 <b>Аргументы:</b> <code>{args_str}</code>"
        )

    async def tool_result(self, tool_name: str, status: str, result: str = "") -> None:
        """Результат выполнения инструмента."""
        icon = "✅" if status == "ok" else "❌" if status == "error" else "⚠️"
        text = f"{icon} <b>Результат {tool_name}:</b> {status}"
        if result:
            result_str = _truncate(result, _MAX_RESULT_LEN)
            text += f"\n<code>{result_str}</code>"
        await self._safe_send(text)

    async def llm_call(self, model: str, prompt_len: int) -> None:
        """Вызов LLM (информационно)."""
        await self._safe_send(
            f"🤖 <b>LLM запрос:</b> {model}\n"
            f"📏 Промпт: {prompt_len} символов"
        )

    async def message(self, text: str) -> None:
        """Произвольное сообщение."""
        await self._safe_send(text)

    # ─── Внутреннее ───────────────────────────────────────────────────────

    async def _safe_send(self, text: str) -> None:
        """Fire-and-forget отправка. Никаких исключений наружу."""
        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.warning("TelegramNotifier: failed to send: %s", e)


def _truncate(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    half = (max_len - 3) // 2
    return s[:half] + "..." + s[-half:]

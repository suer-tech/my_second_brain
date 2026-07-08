import logging
import os
from typing import Optional
import asyncio

from aiogram import Router, types
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart

try:
    from langgraph.errors import GraphRecursionError
except ImportError:  # pragma: no cover

    class GraphRecursionError(Exception):
        pass


from src.agent.graph import build_graph, REACT_RECURSION_LIMIT
from src.agent.utils import is_url

router = Router()
graph = build_graph()

logger = logging.getLogger(__name__)


def _load_allowed_id() -> int:
    """Валидирует ALLOWED_TELEGRAM_ID один раз при импорте (фикс №7).

    Раньше int(os.getenv(...)) вызывался на каждом сообщении и падал с
    ValueError при пустом/некорректном значении в .env, кладя обработчик.
    """
    raw = os.getenv("ALLOWED_TELEGRAM_ID", "").strip()
    if not raw:
        logger.warning(
            "ALLOWED_TELEGRAM_ID не задан — доступ заблокирован для всех пользователей."
        )
        return 0
    try:
        return int(raw)
    except ValueError as e:
        raise RuntimeError(
            f"ALLOWED_TELEGRAM_ID должен быть целым числом, получено: {raw!r}"
        ) from e


ALLOWED_TELEGRAM_ID = _load_allowed_id()


def _user_id(message: types.Message) -> Optional[int]:
    return message.from_user.id if message.from_user else None


@router.message(CommandStart())
async def start_handler(message: types.Message):
    if _user_id(message) != ALLOWED_TELEGRAM_ID:
        await message.answer("У вас нет доступа к этому боту.")
        return

    await message.answer(
        "Привет! Я твой ИИ-Агент ('Второй мозг'). Отправь мне ссылку или задай вопрос, "
        "и я подключу свой LangGraph."
    )


@router.message()
async def process_message(message: types.Message):
    if _user_id(message) != ALLOWED_TELEGRAM_ID:
        return

    if not message.text:
        return

    text = message.text.strip()

    # Игнорируем неизвестные слэш-команды, чтобы они не уходили в LLM-граф
    # как обычный текст (фикс №12). /start обрабатывается отдельным хендлером.
    if text.startswith("/") and not is_url(text):
        await message.answer(
            "Неизвестная команда. Отправь ссылку или текстовый вопрос."
        )
        return

    logger.info("Received message from %s: %s", _user_id(message), text)

    # Отправляем сообщение-плейсхолдер
    processing_msg = await message.answer("Принято! Обрабатываю граф...")

    # Progress callback: отправляет отдельное сообщение при каждом переключении агента.
    async def send_progress(step: str, detail: str):
        try:
            await message.answer(f"<b>{step}</b>\n{detail}", parse_mode=ParseMode.HTML)
        except Exception:
            pass

    # Пробрасываем колбэк в code_loop через модульный глобал (single-user бот,
    # конкурентных запросов нет, поэтому thread-безопасность не нужна).
    from src.agent import code_loop

    code_loop._active_progress = send_progress
    try:
        result = await graph.ainvoke(
            {"input_content": text},
            config={"recursion_limit": REACT_RECURSION_LIMIT},
        )

        final_response = result.get(
            "final_response", "Граф отработал, но ответ не сформирован."
        )
        await processing_msg.edit_text(final_response)

    except GraphRecursionError:
        logger.warning("Graph recursion limit reached for input: %s", text[:200])
        await processing_msg.edit_text(
            "Достигнут лимит шагов рассуждений (ReAct-цикл). "
            "Попробуй переформулировать задачу или разбить её на части."
        )
    except Exception as e:
        logger.error("Error in graph execution: %s", e, exc_info=True)
        await processing_msg.edit_text(
            f"Произошла ошибка при выполнении графа: {str(e)}"
        )
    finally:
        code_loop._active_progress = None

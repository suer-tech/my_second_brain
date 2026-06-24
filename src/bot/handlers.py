from aiogram import Router, types
from aiogram.filters import CommandStart
import logging
import os
from src.agent.graph import build_graph

router = Router()
graph = build_graph()

def get_allowed_id() -> int:
    return int(os.getenv("ALLOWED_TELEGRAM_ID", 0))

@router.message(CommandStart())
async def start_handler(message: types.Message):
    if message.from_user.id != get_allowed_id():
        await message.answer("У вас нет доступа к этому боту.")
        return
        
    await message.answer(
        "Привет! Я твой ИИ-Агент ('Второй мозг'). Отправь мне ссылку или задай вопрос, "
        "и я подключу свой LangGraph."
    )

@router.message()
async def process_message(message: types.Message):
    if message.from_user.id != get_allowed_id():
        return

    if not message.text:
        return
        
    logging.info(f"Received message from {message.from_user.id}: {message.text}")
    
    # Отправляем сообщение-плейсхолдер
    processing_msg = await message.answer("Принято! Обрабатываю граф...")
    
    try:
        # Вызываем граф (асинхронно)
        result = await graph.ainvoke({"input_content": message.text})
        
        # Получаем финальный ответ от узла графа
        final_response = result.get("final_response", "Граф отработал, но ответ не сформирован.")
        
        # Обновляем сообщение для пользователя
        await processing_msg.edit_text(final_response)
        
    except Exception as e:
        logging.error(f"Error in graph execution: {e}", exc_info=True)
        await processing_msg.edit_text(f"Произошла ошибка при выполнении графа: {str(e)}")

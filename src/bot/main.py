import asyncio
import logging
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from src.bot.handlers import router

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)

async def main():
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise ValueError("BOT_TOKEN is not set in .env")

    bot = Bot(token=bot_token)
    dp = Dispatcher()

    # Include routers
    dp.include_router(router)

    # Start polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

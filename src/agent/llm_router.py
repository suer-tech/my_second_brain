import os
from langchain_openai import ChatOpenAI

def get_flash_llm() -> ChatOpenAI:
    """Returns DeepSeek V4 Flash for routine tasks (temporarily set to V4 Pro)."""
    return ChatOpenAI(
        model_name="deepseek/deepseek-v4-pro",
        openai_api_key=os.getenv("OPENROUTER_API_KEY"),
        openai_api_base="https://openrouter.ai/api/v1",
        model_kwargs={
            "extra_body": {
                "reasoning": {"enabled": True}
            }
        }
    )

def get_pro_llm() -> ChatOpenAI:
    """Returns DeepSeek V4 Pro for complex reasoning and architecture tasks via OpenRouter."""
    return ChatOpenAI(
        model_name="deepseek/deepseek-v4-pro", # Указываем правильный ID модели
        openai_api_key=os.getenv("OPENROUTER_API_KEY"),
        openai_api_base="https://openrouter.ai/api/v1",
        model_kwargs={
            "extra_body": {
                "reasoning": {"enabled": True}
            }
        }
    )

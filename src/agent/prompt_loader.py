"""Загрузчик промптов из Markdown-файлов.

Промпты хранятся в prompts/ рядом с корнем проекта.
Поддерживает шаблонные переменные через str.format().
"""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
PROMPTS_DIR = os.path.join(BASE_DIR, "prompts")


def load_prompt(name: str, **kwargs) -> str:
    """Загружает промпт из prompts/{name}.md и подставляет переменные.

    Args:
        name: путь к файлу без расширения, например 'agents/chat' или 'system/safety'.
        **kwargs: переменные для подстановки через str.format().
    """
    path = os.path.join(PROMPTS_DIR, f"{name}.md")
    with open(path, "r", encoding="utf-8") as f:
        template = f.read()
    if kwargs:
        return template.format(**kwargs)
    return template

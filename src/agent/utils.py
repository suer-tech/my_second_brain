import os
import glob
import uuid
from typing import Optional

import aiohttp
import trafilatura
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
WIKI_DIR = os.path.join(BASE_DIR, "wiki")
RAW_DIR = os.path.join(BASE_DIR, "raw")
MEMORY_DIR = os.path.join(BASE_DIR, "memory")

# Категории личного хранилища пользователя («копия пользователя»).
# Каждой категории соответствует файл в memory/.
MEMORY_CATEGORIES: dict[str, str] = {
    "facts": "facts.md",  # факты о мире, концепции
    "preferences": "preferences.md",  # предпочтения, вкусы, привычки
    "people": "people.md",  # люди, коллеги, контакты
    "projects": "projects.md",  # проекты, работы, статусы
}

# Лимит суммарного размера Wiki-контекста, подаваемого в LLM (символы).
# Защищает от переполнения контекстного окна при росте базы знаний (фикс №4).
WIKI_CONTEXT_MAX_CHARS = 20000

# Создаём каталоги хранилища один раз при импорте модуля (фикс №2).
# Раньше это делалось в каждой функции записи — лишний syscall на каждый чекпоинт.
os.makedirs(RAW_DIR, exist_ok=True)
os.makedirs(WIKI_DIR, exist_ok=True)
os.makedirs(MEMORY_DIR, exist_ok=True)


def read_user_profile() -> str:
    """Reads the user knowledge map for personalization."""
    profile_path = os.path.join(WIKI_DIR, "user_knowledge_map.md")
    if os.path.exists(profile_path):
        with open(profile_path, "r", encoding="utf-8") as f:
            return f.read()
    return "User profile not found."


def update_user_profile(new_goals: str) -> None:
    """Appends new goals, intentions, and experience to the user knowledge map.

    Создаёт файл профиля при отсутствии (фикс №5), иначе извлечённые цели
    молча терялись, а узел графа отчитывался успехом.
    """
    profile_path = os.path.join(WIKI_DIR, "user_knowledge_map.md")
    header = ""
    if not os.path.exists(profile_path):
        header = "# Профиль пользователя и Карта знаний (Knowledge Map)\n\n"
    with open(profile_path, "a", encoding="utf-8") as f:
        f.write(f"{header}## Дополнения к профилю (Опыт и Цели)\n{new_goals}\n")


def read_all_wiki(max_chars: int = WIKI_CONTEXT_MAX_CHARS) -> str:
    """Reads markdown files in wiki directory as context for Q&A.

    Ограничивает суммарный размер контекста (фикс №4), чтобы не переполнить
    контекстное окно модели при росте Wiki. Файлы обрезаются пропорционально,
    если суммарный объём превышает max_chars.
    """
    files: list[tuple[str, str]] = []
    total = 0
    for filepath in sorted(glob.glob(os.path.join(WIKI_DIR, "*.md"))):
        # Skip user profile to avoid duplicating context if handled separately
        if "user_knowledge_map.md" in filepath:
            continue
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        header = f"--- File: {os.path.basename(filepath)} ---\n"
        files.append((header, text))
        total += len(header) + len(text)

    if not files:
        return ""

    # Если вписываемся в лимит — отдаём как есть.
    if total <= max_chars:
        return "\n\n".join(h + t for h, t in files)

    # Иначе сжимаем: каждый файл получает одинаковую долю бюджета.
    per_file_budget = max(500, max_chars // len(files))
    chunks: list[str] = []
    for header, text in files:
        budget = per_file_budget - len(header)
        if budget < len(text):
            text = text[:budget].rsplit(" ", 1)[0] + "\n…[обрезано]…"
        chunks.append(header + text)
    result = "\n\n".join(chunks)
    return result[:max_chars]


async def fetch_url_text(url: str) -> Optional[str]:
    """Асинхронно (фикс №8) скачивает URL и извлекает текст через trafilatura.

    Возвращает None при ошибке вместо строкового сообщения (фикс №10),
    чтобы вызывающий узел мог корректно прервать ingest-ветку, а не скармливать
    LLM текст «Could not download URL.» как «статью».
    """
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text(errors="ignore")
        text = trafilatura.extract(html)
        return text or None
    except Exception:
        return None


def is_url(text: str) -> bool:
    try:
        result = urlparse(text.strip())
        return all([result.scheme in ("http", "https"), result.netloc])
    except ValueError:
        return False


def save_raw_file(content: str) -> str:
    """Saves raw content to the raw/ directory."""
    filename = f"{uuid.uuid4().hex}.txt"
    filepath = os.path.join(RAW_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath


def save_wiki_file(title: str, content: str) -> str:
    """Saves compiled markdown to the wiki/ directory."""
    # Sanitize title for filename
    safe_title = (
        "".join([c if c.isalnum() else "_" for c in title]).strip("_") or "article"
    )
    # Добавляем короткий uuid-суффикс во избежание коллизий имён (фикс №1).
    unique_suffix = uuid.uuid4().hex[:8]
    filepath = os.path.join(WIKI_DIR, f"{safe_title}_{unique_suffix}.md")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath


def read_all_memory() -> str:
    """Читает все файлы из memory/ — личное хранилище пользователя («копия пользователя»).

    Используется в системном промпте qa_pro, чтобы агент «помнил» всё, что
    пользователь считает важным: факты, предпочтения, людей, проекты.
    """
    parts: list[str] = []
    for category, filename in MEMORY_CATEGORIES.items():
        filepath = os.path.join(MEMORY_DIR, filename)
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                parts.append(f"--- {category.upper()} ---\n{content}")
    return "\n\n".join(parts) if parts else "Memory is empty."


def update_memory(category: str, content: str) -> None:
    """Дописывает новую информацию в файл соответствующей категории памяти.

    Если категория неизвестна — сохраняет в facts.md (fallback).
    """
    filename = MEMORY_CATEGORIES.get(category, "facts.md")
    filepath = os.path.join(MEMORY_DIR, filename)
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(f"\n{content}\n")

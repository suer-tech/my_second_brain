"""Менеджер схемы сопоставления raw ↔ wiki и перекрёстных ссылок.

Схема хранится в schema/index.json — единый JSON-файл, который:
  - связывает каждый raw-файл с соответствующим wiki-файлом;
  - хранит теги и краткое содержание для поиска;
  - хранит перекрёстные ссылки (links) между статьями по тегам.

Структура index.json:
{
  "version": 1,
  "articles": [
    {
      "id": "art_abc123",           # уникальный ID статьи
      "raw_file": "raw/abc123.txt",  # путь к оригиналу
      "wiki_file": "wiki/Article_abc123_xyz.md",  # путь к выжимке
      "title": "LangGraph Routing",
      "source_url": "https://..." или null,
      "created_at": "2024-01-01T12:00:00Z",
      "tags": ["langgraph", "routing", "state"],
      "links": ["art_def456"],       # ID связанных статей
      "summary": "Краткое описание статьи в одно предложение"
    }
  ]
}
"""

import os
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SCHEMA_DIR = os.path.join(BASE_DIR, "schema")
INDEX_PATH = os.path.join(SCHEMA_DIR, "index.json")

os.makedirs(SCHEMA_DIR, exist_ok=True)


def load_index() -> dict:
    """Загружает индекс сопоставления raw ↔ wiki."""
    if not os.path.exists(INDEX_PATH):
        return {"version": 1, "articles": []}
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "articles": []}


def save_index(index: dict) -> None:
    """Атомарно сохраняет индекс."""
    tmp_path = INDEX_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, INDEX_PATH)


def add_article(
    raw_file: str,
    wiki_file: str,
    title: str,
    tags: list[str],
    summary: str,
    source_url: Optional[str] = None,
) -> str:
    """Добавляет статью в индекс и возвращает её ID.

    Автоматически вычисляет перекрёстные ссылки: ищет существующие статьи
    с пересекающимися тегами и связывает их двунаправленно.
    """
    index = load_index()
    article_id = f"art_{uuid.uuid4().hex[:12]}"

    # Ищем связанные статьи по пересечению тегов.
    related_ids: list[str] = []
    tags_lower = {t.lower() for t in tags}
    for existing in index["articles"]:
        existing_tags = {t.lower() for t in existing.get("tags", [])}
        if tags_lower & existing_tags:
            related_ids.append(existing["id"])

    article = {
        "id": article_id,
        "raw_file": raw_file,
        "wiki_file": wiki_file,
        "title": title.strip()[:200],
        "source_url": source_url,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tags": tags,
        "links": related_ids,
        "summary": summary.strip()[:300],
    }
    index["articles"].append(article)

    # Обновляем обратные ссылки: добавляем нашу статью в links связанных.
    for rid in related_ids:
        for existing in index["articles"]:
            if existing["id"] == rid and article_id not in existing["links"]:
                existing["links"].append(article_id)

    save_index(index)
    return article_id


def find_related(tags: list[str], limit: int = 5) -> list[dict]:
    """Находит статьи с пересекающимися тегами (без учёта самой новой).

    Возвращает список словарей с id, title, wiki_file, tags — для формирования
    секции «Связанные материалы» в wiki-статье.
    """
    index = load_index()
    tags_lower = {t.lower() for t in tags}
    scored: list[tuple[int, dict]] = []
    for article in index["articles"]:
        existing_tags = {t.lower() for t in article.get("tags", [])}
        overlap = len(tags_lower & existing_tags)
        if overlap > 0:
            scored.append((overlap, article))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [a for _, a in scored[:limit]]


def get_article_by_wiki(wiki_file: str) -> Optional[dict]:
    """Находит запись в индексе по пути к wiki-файлу."""
    index = load_index()
    for article in index["articles"]:
        if article.get("wiki_file") == wiki_file:
            return article
    return None


def get_article_by_raw(raw_file: str) -> Optional[dict]:
    """Находит запись в индексе по пути к raw-файлу."""
    index = load_index()
    for article in index["articles"]:
        if article.get("raw_file") == raw_file:
            return article
    return None

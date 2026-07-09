import json
import uuid as _uuid
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from src.agent.skills.base import BaseSkill, SkillContext
from src.agent.utils import (
    is_url,
    fetch_url_text,
    save_raw_file,
    save_wiki_file,
    read_user_profile,
)
from src.agent import schema as schema_manager
from src.agent.llm_router import get_flash_llm, get_pro_llm
from src.agent.prompt_loader import load_prompt
from src.agent.logger import get_session_id, log_node_start, log_node_end

logger = logging.getLogger(__name__)


class IngestSkill(BaseSkill):
    name = "ingest"
    description = "Сохранение статей, ссылок и текстов в Wiki"

    async def execute(self, task: str, context: SkillContext) -> str:
        session_id = context.session_id or get_session_id() or "unknown"
        start_ms = log_node_start(session_id, "ingest", branch="ingest")

        if context.progress:
            await context.progress("📥 Ingest", "загружаю и анализирую контент...")

        # ─── Шаг 1: загрузка ──────────────────────────────────────────────

        source_url: str | None = None
        if is_url(task):
            source_url = task
            text = await fetch_url_text(task)
            if not text:
                log_node_end(session_id, "ingest", start_ms, branch="ingest", status="error")
                return "Не удалось загрузить или извлечь текст по ссылке. Проверь URL."
        else:
            text = task

        raw_path = save_raw_file(text)

        # ─── Шаг 2: извлечение фактов (Flash LLM) ─────────────────────────

        profile = read_user_profile()
        flash_llm = get_flash_llm()

        extract_response = await flash_llm.ainvoke([
            SystemMessage(content=load_prompt("agents/extract_flash", profile=profile)),
            HumanMessage(content=text[:10000]),
        ])
        raw = str(extract_response.content).strip()

        summary = raw
        tags: list[str] = []
        try:
            json_str = raw
            if "```" in raw:
                start = raw.find("{")
                end = raw.rfind("}") + 1
                if start >= 0 and end > start:
                    json_str = raw[start:end]
            parsed = json.loads(json_str)
            summary = parsed.get("summary", raw)
            tags = parsed.get("tags", [])
        except (json.JSONDecodeError, ValueError):
            pass

        if context.progress:
            await context.progress("📥 Ingest", f"извлекаю факты ({len(tags)} тегов), компилирую статью...")

        # ─── Шаг 3: компиляция статьи (Pro LLM) ───────────────────────────

        pro_llm = get_pro_llm()

        related = schema_manager.find_related(tags, limit=5)
        related_context = ""
        if related:
            related_titles = [f"- {a['title']} ({a['wiki_file']})" for a in related]
            related_context = "\n\nRelated existing articles in Wiki:\n" + "\n".join(related_titles)

        compile_response = await pro_llm.ainvoke([
            SystemMessage(content=load_prompt("agents/compile_pro", profile=profile, related_context=related_context)),
            HumanMessage(content=summary),
        ])
        wiki_content = str(compile_response.content)

        title = summary[:60].strip().split("\n")[0] or f"Article_{_uuid.uuid4().hex[:8]}"
        wiki_path = save_wiki_file(title, wiki_content)

        article_id = schema_manager.add_article(
            raw_file=raw_path,
            wiki_file=wiki_path,
            title=title,
            tags=tags,
            summary=summary[:200].replace("\n", " "),
            source_url=source_url,
        )

        if related:
            links_md = "\n\n## Связанные материалы\n"
            for a in related:
                links_md += f"- [[{a['wiki_file']}]] {a['title']}\n"
            with open(wiki_path, "a", encoding="utf-8") as f:
                f.write(links_md)

        log_node_end(
            session_id, "ingest", start_ms, branch="ingest",
            data={"wiki_path": wiki_path, "article_id": article_id, "tags": tags},
        )

        if context.progress:
            await context.progress(
                "📥 Ingest",
                f"Статья сохранена: {wiki_path} ({len(tags)} тегов, {len(related)} связей)",
            )

        return (
            f"Статья сохранена в Wiki: {wiki_path}\n"
            f"ID в схеме: {article_id}\n"
            f"Теги: {', '.join(tags) if tags else 'нет'}\n"
            f"Связанных статей: {len(related)}\n\n"
            f"Краткое ревью:\n{wiki_content[:500]}..."
        )

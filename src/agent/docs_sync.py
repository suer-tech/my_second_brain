"""Детерминированная актуализация документации после правок кода.

Алгоритм (согласно решению пользователя):
  1. ДО запуска orchestrator делаем snapshot состояния git (список изменённых файлов).
  2. ПОСЛЕ завершения задачи делаем второй snapshot.
  3. Разность (файлы, изменившиеся в течение задачи) — наш вход.
  4. Если есть изменённые .py файлы → LLM анализирует docs/wiki/ и обновляет релевантную документацию.
  5. Защита от рекурсии: .md-файлы, изменённые sync-функцией, не триггерят повторный sync.

Git используется как единственный источник истины — никакого угадывания LLM о том,
менялось ли что-то.
"""

import os
import asyncio
import logging
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage

from src.agent.llm_router import get_pro_llm
from src.agent.tools import read_file

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
DOCS_WIKI_DIR = os.path.join(BASE_DIR, "docs", "wiki")


async def _git_changed_files() -> set[str]:
    """Возвращает множество путей изменённых/новых файлов (git status, porcelain).

    Включает staged + unstaged + untracked. Исключает deleted (нам важны только
    новые правки, а не удаление). Запускается через async subprocess.
    """
    try:
        proc = await asyncio.create_subprocess_shell(
            "git status --porcelain",
            cwd=BASE_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        stdout = out.decode("utf-8", errors="ignore")
    except Exception as e:
        logger.warning("git status failed: %s", e)
        return set()

    changed: set[str] = set()
    for line in stdout.splitlines():
        if len(line) < 4:
            continue
        status = line[:2]
        path = line[3:].strip().strip('"')
        # Пропускаем удалённые файлы (D в статусе).
        if "D" in status:
            continue
        changed.add(path)
    return changed


async def capture_git_snapshot() -> set[str]:
    """Делает snapshot изменённых файлов ДО запуска задачи."""
    return await _git_changed_files()


async def get_task_changed_files(before: set[str]) -> set[str]:
    """Вычисляет файлы, изменившиеся В ТЕЧЕНИЕ задачи (разность after минус before).

    Исключает .md-файлы: их может менять сама docs_sync, и это не должно
    триггерить рекурсивный анализ исходного кода.
    """
    after = await _git_changed_files()
    diff = after - before
    # Только исходный код триггерит актуализацию доки.
    return {p for p in diff if not p.endswith(".md")}


async def _list_docs_wiki() -> list[str]:
    """Возвращает список .md файлов в docs/wiki/."""
    if not os.path.isdir(DOCS_WIKI_DIR):
        return []
    return [f for f in os.listdir(DOCS_WIKI_DIR) if f.endswith(".md")]


async def sync_documentation(changed_files: set[str]) -> Optional[str]:
    """LLM анализирует изменённые файлы и обновляет релевантную документацию.

    Возвращает отчёт об обновлённой документации (или None, если нечего обновлять).
    LLM сама выбирает, какой .md-файл обновить, анализируя:
      - список изменившихся исходных файлов
      - их содержимое (через read_file tool)
      - список доступной документации в docs/wiki/
      - содержимое доки (через read_file tool)
    """
    if not changed_files:
        return None

    # Фильтруем только файлы внутри проекта (отсекаем sessions/, raw/, wiki/).
    relevant_files = sorted(
        p for p in changed_files if p.startswith("src/") or p.startswith("docs/")
    )
    if not relevant_files:
        return None

    docs_files = await _list_docs_wiki()
    if not docs_files:
        logger.info("Нет документации для актуализации")
        return None

    files_list = "\n".join(f"- {p}" for p in relevant_files)
    docs_list = "\n".join(f"- docs/wiki/{f}" for f in docs_files)

    sys_msg = SystemMessage(
        content=(
            "Ты — Documentation Sync Agent. Твоя задача — актуализировать документацию "
            "проекта после изменений в исходном коде.\n\n"
            f"ИЗМЕНИВШИЕСЯ ФАЙЛЫ:\n{files_list}\n\n"
            f"ДОСТУПНАЯ ДОКУМЕНТАЦИЯ:\n{docs_list}\n\n"
            "АЛГОРИТМ:\n"
            "1. Прочитай изменившиеся файлы (read_file), чтобы понять суть изменений.\n"
            "2. Прочитай релевантные .md-файлы из docs/wiki/ (read_file).\n"
            "3. Реши, нужно ли обновить документацию. Если да — обнови через write_file.\n"
            "4. Если изменения не требуют обновления доки (например, рефакторинг без "
            "изменения API) — ничего не делай.\n\n"
            "ПРАВИЛА:\n"
            "- Обновляй ТОЛЬКО файлы в docs/wiki/. Никогда не трогай исходный код.\n"
            "- Сохраняй структуру и стиль существующей документации.\n"
            "- Если обновил доку — верни краткий отчёт: какие файлы и что изменилось.\n"
            "- Если обновление не требуется — верни ТОЛЬКО: NO_UPDATE_NEEDED"
        )
    )
    user_msg = HumanMessage(
        content="Проанализируй изменения и актуализируй документацию."
    )

    from src.agent.tools import write_file

    llm_with_write = get_pro_llm().bind_tools([read_file, write_file])

    response = await llm_with_write.ainvoke([sys_msg, user_msg])
    result = str(response.content).strip()

    if "NO_UPDATE_NEEDED" in result:
        logger.info("Documentation sync: обновление не требуется")
        return None

    logger.info("Documentation sync: %s", result[:200])
    return result

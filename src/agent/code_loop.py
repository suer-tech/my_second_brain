"""Агентный луп для задач модификации кода.

Архитектура:
  Orchestrator (оркестратор) координирует цикл:
    1. Planner    — изучает задачу, составляет план, сохраняет в sessions/{id}/plan.md
    2. Developer  — следует плану, вносит правки в код через tools (ReAct-цикл)
    3. Checker    — пишет тесты, тестирует исправленный код (ReAct-цикл)
    4. Если тесты упали → обратно к Developer (шаг 2), цикл повторяется
    5. Если тесты прошли → Docs Sync → результат пользователю

На каждом переключении агента отправляется progress-сообщение в Telegram.
По завершении выводится статистика использованных тулов.
"""

import os
import shutil
import uuid as _uuid
import logging
from collections import Counter
from typing import Optional, Callable, Awaitable, Any

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from src.agent.llm_router import get_pro_llm
from src.agent.tools import developer_tools
from src.agent.docs_sync import (
    capture_git_snapshot,
    get_task_changed_files,
    sync_documentation,
)

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")

MAX_ITERATIONS = 3
MAX_KEPT_SESSIONS = 10
MAX_TOOL_ITERS = 8  # макс. вызовов тулов в одном агенте

os.makedirs(SESSIONS_DIR, exist_ok=True)

# Тип для callback прогресса: (заголовок, деталь) → None
ProgressCallback = Callable[[str, str], Awaitable[None]]

# Модульный глобал для прогресс-колбэка. Устанавливается из handlers.py
# перед вызовом графа, сбрасывается после. Single-user бот — конкурентных
# запросов нет, отдельный lock не нужен.
_active_progress: Optional[ProgressCallback] = None


def _create_session_dir() -> str:
    session_id = _uuid.uuid4().hex[:12]
    session_dir = os.path.join(SESSIONS_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    return session_dir


def _purge_old_sessions() -> None:
    try:
        entries = [
            (
                os.path.join(SESSIONS_DIR, d),
                os.path.getmtime(os.path.join(SESSIONS_DIR, d)),
            )
            for d in os.listdir(SESSIONS_DIR)
            if os.path.isdir(os.path.join(SESSIONS_DIR, d))
        ]
        entries.sort(key=lambda x: x[1], reverse=True)
        for path, _ in entries[MAX_KEPT_SESSIONS:]:
            shutil.rmtree(path, ignore_errors=True)
            logger.info("Purged old session: %s", os.path.basename(path))
    except Exception as e:
        logger.warning("Session purge failed: %s", e)


async def _run_with_tools(
    llm,
    system_prompt: str,
    user_prompt: str,
    tools: list,
    tool_counter: Counter,
    max_iters: int = MAX_TOOL_ITERS,
) -> str:
    """Выполняет LLM с инструментами в цикле (ReAct) до получения текстового ответа.

    Каждое исполнение тула записывается в tool_counter для итоговой статистики.
    """
    messages: list[BaseMessage] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]
    llm_with_tools = llm.bind_tools(tools)

    for _ in range(max_iters):
        response = await llm_with_tools.ainvoke(messages)
        messages.append(response)

        if not response.tool_calls:
            return str(response.content) if response.content else ""

        # Исполняем тулы, считаем.
        tool_results: list[ToolMessage] = []
        for tc in response.tool_calls:
            name = tc["name"]
            args = tc.get("args", {})
            tool_counter[name] += 1
            logger.debug("Tool call: %s(%s)", name, args)

            # Находим тул по имени.
            tool_fn = None
            for t in tools:
                if t.name == name:
                    tool_fn = t
                    break

            if tool_fn:
                try:
                    result = await tool_fn.ainvoke(args)
                except Exception as e:
                    result = f"Error executing {name}: {e}"
            else:
                result = f"Unknown tool: {name}"

            tool_results.append(
                ToolMessage(
                    content=str(result),
                    tool_call_id=tc["id"],
                    name=name,
                )
            )

        messages.extend(tool_results)

    # Исчерпан лимит итераций — возвращаем что есть.
    last = messages[-1]
    return str(last.content) if getattr(last, "content", None) else ""


async def run_planner(
    task: str,
    session_dir: str,
    progress: Optional[ProgressCallback] = None,
    tool_counter: Optional[Counter] = None,
) -> str:
    """Planner: изучает задачу, составляет план, сохраняет в plan.md."""
    llm = get_pro_llm()
    tc = tool_counter or Counter()

    if progress:
        await progress(
            "📋 Planner", "Анализирую задачу, изучаю код проекта, составляю план..."
        )

    system_prompt = (
        "Ты — Planner агент. Твоя задача — изучить запрос пользователя на модификацию кода, "
        "проанализировать архитектуру проекта (используй read_file для чтения файлов), "
        "и составить детальный пошаговый план реализации.\n\n"
        "План должен включать:\n"
        "1. Анализ текущего состояния кода (какие файлы затронуты)\n"
        "2. Пошаговые действия для реализации\n"
        "3. Потенциальные риски и как их избежать\n"
        "4. Критерии приёмки (когда задача считается выполненной)\n\n"
        "Сохрани план через write_file в файл plan.md в указанной директории сессии."
    )
    user_prompt = (
        f"Задача: {task}\n\n"
        f"Директория сессии: {session_dir}\n"
        f"Составь план и сохрани его в {session_dir}/plan.md"
    )

    # Planner имеет только read_file + write_file (план — .md)
    from src.agent.tools import read_file, write_file

    planner_tools = [read_file, write_file]

    result = await _run_with_tools(llm, system_prompt, user_prompt, planner_tools, tc)

    # Гарантированно сохраняем план.
    plan_path = os.path.join(session_dir, "plan.md")
    if not os.path.exists(plan_path) or os.path.getsize(plan_path) < 50:
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write(result)

    logger.info("Planner сохранил план: %s", plan_path)
    if progress:
        await progress("📋 Planner", f"План готов, сохранён в {plan_path}")

    return plan_path


async def run_developer(
    task: str,
    plan_path: str,
    feedback: Optional[str] = None,
    progress: Optional[ProgressCallback] = None,
    tool_counter: Optional[Counter] = None,
) -> str:
    """Developer: следует плану, вносит правки в код через ReAct-цикл."""
    llm = get_pro_llm()
    tc = tool_counter or Counter()

    plan_content = ""
    if os.path.exists(plan_path):
        with open(plan_path, "r", encoding="utf-8") as f:
            plan_content = f.read()

    detail = "Вношу правки в код по плану..."
    if feedback:
        detail = "Исправляю ошибки, найденные тестировщиком..."

    if progress:
        await progress("👨‍💻 Developer", detail)

    system_prompt = (
        "Ты — Developer агент. Следуй плану из plan.md и вноси правки в код.\n"
        "Используй инструменты: read_file, write_file, execute_bash_command, list_directory.\n"
        "После каждого изменения проверяй результат (read_file, bash).\n"
        "Когда все правки сделаны — верни краткий отчёт: какие файлы изменены и что сделано.\n\n"
        f"План:\n{plan_content}\n\n"
        f"Задача: {task}\n"
    )
    if feedback:
        system_prompt += (
            f"\n\n⚠️ Тестировщик нашёл ошибки. Вот feedback:\n{feedback}\n"
            "Исправь код согласно этому feedback и попробуй снова."
        )

    user_prompt = "Приступай к реализации по плану."

    result = await _run_with_tools(llm, system_prompt, user_prompt, developer_tools, tc)

    logger.info("Developer завершил: %s", result[:200])
    if progress:
        await progress("👨‍💻 Developer", f"Завершил: {result[:150]}")

    return result


async def run_checker(
    task: str,
    plan_path: str,
    progress: Optional[ProgressCallback] = None,
    tool_counter: Optional[Counter] = None,
) -> tuple[bool, str]:
    """Checker: составляет тесты, тестирует код, возвращает (success, feedback)."""
    llm = get_pro_llm()
    tc = tool_counter or Counter()

    plan_content = ""
    if os.path.exists(plan_path):
        with open(plan_path, "r", encoding="utf-8") as f:
            plan_content = f.read()

    if progress:
        await progress("🧪 Checker", "Пишу тесты и запускаю их...")

    system_prompt = (
        "Ты — Checker агент (тестировщик). Твоя задача — проверить, что код "
        "был правильно изменён согласно плану.\n\n"
        "1. Прочитай план из plan.md (read_file)\n"
        "2. Прочитай изменённый код (read_file)\n"
        "3. Напиши тесты (write_file) и запусти их (execute_bash_command)\n"
        "4. Проанализируй результат\n\n"
        "Если тесты прошли успешно — ответь строго: TESTS_PASSED\n"
        "Если тесты упали — ответь TESTS_FAILED, затем подробно опиши ошибки.\n\n"
        f"План:\n{plan_content}\n\n"
        f"Задача: {task}"
    )
    user_prompt = "Проверь код и запусти тесты."

    result = await _run_with_tools(llm, system_prompt, user_prompt, developer_tools, tc)

    if "TESTS_PASSED" in result:
        logger.info("Checker: тесты прошли")
        if progress:
            await progress("🧪 Checker", "✅ Все тесты пройдены успешно")
        return True, ""
    else:
        logger.info("Checker: тесты упали")
        if progress:
            await progress(
                "🧪 Checker", "❌ Тесты упали, возвращаю Developer'у на доработку"
            )
        return False, result


def _format_tool_stats(tool_counter: Counter) -> str:
    """Форматирует статистику использованных тулов для ответа пользователю."""
    if not tool_counter:
        return ""
    lines = []
    for name, count in tool_counter.most_common():
        lines.append(f"  • {name}: ×{count}")
    return "🛠 **Использовано инструментов:**\n" + "\n".join(lines)


async def run_orchestrator(
    task: str,
    progress: Optional[ProgressCallback] = None,
) -> str:
    """Оркестратор: координирует Planner → Developer → Checker с циклом.

    Принимает опциональный progress-колбэк. Если не передан — использует
    модульный глобал _active_progress (устанавливается из handlers.py).
    Возвращает финальный отчёт с tool-статистикой.
    """
    if progress is None:
        progress = _active_progress

    session_dir = _create_session_dir()
    _purge_old_sessions()

    tool_counter: Counter = Counter()

    if progress:
        await progress("🚀 Orchestrator", f"Запускаю пайплайн. Задача: {task[:120]}")

    logger.info("Orchestrator: сессия %s, задача: %s", session_dir, task[:200])

    # Snapshot git ДО задачи.
    git_before = await capture_git_snapshot()

    # Шаг 1: Planner
    plan_path = await run_planner(task, session_dir, progress, tool_counter)

    # Шаг 2-3: Developer → Checker (цикл)
    feedback: Optional[str] = None
    dev_report = ""
    checker_feedback = ""
    success = False
    iterations_used = 0
    for iteration in range(1, MAX_ITERATIONS + 1):
        iterations_used = iteration
        logger.info("Orchestrator: итерация %d", iteration)

        dev_report = await run_developer(
            task, plan_path, feedback, progress, tool_counter
        )
        success, checker_feedback = await run_checker(
            task, plan_path, progress, tool_counter
        )

        if success:
            break
        feedback = checker_feedback

    # Docs Sync
    docs_report = ""
    try:
        changed = await get_task_changed_files(git_before)
        if changed:
            if progress:
                await progress(
                    "📚 Docs Sync",
                    f"Актуализирую документацию ({len(changed)} изм. файлов)...",
                )
            sync_result = await sync_documentation(changed)
            if sync_result:
                docs_report = (
                    f"\n\n📚 [Документация актуализирована]:\n{sync_result[:400]}"
                )
            if progress:
                await progress("📚 Docs Sync", "Готово")
    except Exception as e:
        logger.warning("Docs sync failed: %s", e)

    # Статистика тулов
    stats = _format_tool_stats(tool_counter)

    # Финальный ответ
    if success:
        result = (
            f"✅ **Задача выполнена успешно!**\n\n"
            f"Сессия: `{session_dir}`\n"
            f"План: {plan_path}\n"
            f"Итераций: {iterations_used}\n\n"
            f"Отчёт разработчика:\n{dev_report[:500]}\n\n"
            f"{stats}"
            f"{docs_report}"
        )
    else:
        result = (
            f"⚠️ **Не удалось выполнить задачу за {MAX_ITERATIONS} итераций.**\n\n"
            f"Сессия: `{session_dir}`\n"
            f"План: {plan_path}\n\n"
            f"Последний отчёт разработчика:\n{dev_report[:500]}\n\n"
            f"Feedback тестировщика:\n{checker_feedback[:500]}\n\n"
            f"{stats}"
            f"{docs_report}"
        )

    if progress:
        await progress(
            "🏁 Orchestrator",
            f"Пайплайн завершён. Итераций: {iterations_used}, тулов: {sum(tool_counter.values())}",
        )

    return result

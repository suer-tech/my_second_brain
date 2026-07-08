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
    """Оркестратор — LLM-агент, руководящий Planner/Developer/Checker.

    Это полноценный LLM-агент с ReAct-циклом. Sub-agents (Planner, Developer,
    Checker) вызываются как инструменты. Оркестратор сам решает:
      - когда вызвать каждого агента
      - нужно ли повторять цикл Developer→Checker после неудачных тестов
      - когда задача завершена и пора сформировать финальный отчёт
    """
    if progress is None:
        progress = _active_progress

    session_dir = _create_session_dir()
    _purge_old_sessions()

    tool_counter: Counter = Counter()

    # Общее состояние между Orchestrator и sub-agents.
    shared = {"plan_path": "", "feedback": None, "success": False, "iterations": 0}

    if progress:
        await progress("🚀 Orchestrator", f"Запускаю пайплайн. Задача: {task[:120]}")

    logger.info("Orchestrator: сессия %s, задача: %s", session_dir, task[:200])

    # ─── Sub-agent tools для Orchestrator ──────────────────────────────

    from langchain_core.tools import tool as tool_decorator

    @tool_decorator
    async def call_planner() -> str:
        """Вызвать Planner агента: анализирует задачу, изучает код, составляет план в plan.md.
        Вызывай ПЕРВЫМ, до Developer и Checker."""
        if progress:
            await progress(
                "📋 Planner", "Анализирую задачу, изучаю код проекта, составляю план..."
            )
        plan_path = await run_planner(task, session_dir, progress, tool_counter)
        shared["plan_path"] = plan_path
        if progress:
            await progress("📋 Planner", f"План готов: {plan_path}")
        return f"План создан и сохранён: {plan_path}"

    @tool_decorator
    async def call_developer(feedback: str = "") -> str:
        """Вызвать Developer агента: вносит правки в код согласно плану.
        Аргумент feedback передай, если Checker нашёл ошибки (TESTS_FAILED).
        Вызывай ПОСЛЕ Planner и после каждого провала тестов."""
        shared["iterations"] += 1
        detail = (
            "Вношу правки в код по плану..."
            if not feedback
            else "Исправляю ошибки по feedback..."
        )
        if progress:
            await progress("👨‍💻 Developer", detail)
        report = await run_developer(
            task, shared["plan_path"], feedback or None, progress, tool_counter
        )
        if progress:
            await progress("👨‍💻 Developer", f"Завершил: {report[:150]}")
        return report

    @tool_decorator
    async def call_checker() -> str:
        """Вызвать Checker агента: пишет тесты, тестирует код.
        Возвращает 'TESTS_PASSED' или 'TESTS_FAILED: ...'.
        Вызывай ПОСЛЕ Developer."""
        if progress:
            await progress("🧪 Checker", "Пишу тесты и запускаю их...")
        success, feedback = await run_checker(
            task, shared["plan_path"], progress, tool_counter
        )
        shared["feedback"] = feedback
        shared["success"] = success
        if success:
            if progress:
                await progress("🧪 Checker", "✅ Все тесты пройдены")
            return "TESTS_PASSED"
        else:
            if progress:
                await progress("🧪 Checker", "❌ Тесты упали, нужно доработать")
            return f"TESTS_FAILED: {feedback}"

    orchestrator_tools = [call_planner, call_developer, call_checker]

    # ─── Git snapshot ДО ───────────────────────────────────────────────

    git_before = await capture_git_snapshot()

    # ─── Orchestrator LLM ReAct-цикл ────────────────────────────────────

    llm = get_pro_llm()

    system_prompt = (
        "Ты — Orchestrator агент. Ты руководишь процессом модификации кода. "
        "У тебя есть три подчинённых агента, вызываемых как инструменты:\n\n"
        "1. call_planner — анализирует задачу, изучает код, составляет план\n"
        "2. call_developer — следует плану, вносит правки в код\n"
        "3. call_checker — пишет тесты, тестирует код\n\n"
        "АЛГОРИТМ:\n"
        "1. Сначала вызови call_planner\n"
        "2. Затем call_developer\n"
        "3. Затем call_checker\n"
        "4. Если checker вернул TESTS_FAILED — вызови call_developer снова, "
        "передав feedback из ответа checker\n"
        "5. Повторяй цикл Developer→Checker пока тесты не пройдут "
        "(максимум 3 полные итерации)\n"
        "6. Когда checker вернёт TESTS_PASSED (или после 3 неудач) — "
        "сформируй финальный отчёт для пользователя\n\n"
        "ВАЖНО: Ты сам принимаешь решения. Анализируй результат каждого агента. "
        "Если Developer сообщает об ошибке, передай это в feedback для следующей итерации."
    )

    # Макс. итераций: planner(1) + 3×(developer+checker)(6) + final(1) = 8, берём 12.
    MAX_ORCHESTRATOR_ITERS = 12

    orch_result = await _run_with_tools(
        llm,
        system_prompt,
        f"Задача пользователя: {task}",
        orchestrator_tools,
        tool_counter,
        max_iters=MAX_ORCHESTRATOR_ITERS,
    )

    # ─── Docs Sync ─────────────────────────────────────────────────────

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

    # ─── Финальный отчёт ────────────────────────────────────────────────

    stats = _format_tool_stats(tool_counter)

    if shared["success"]:
        result = (
            f"✅ **Задача выполнена успешно!**\n\n"
            f"Сессия: `{session_dir}`\n"
            f"План: {shared['plan_path']}\n"
            f"Итераций: {shared['iterations']}\n\n"
            f"Отчёт Orchestrator:\n{orch_result[:600]}\n\n"
            f"{stats}"
            f"{docs_report}"
        )
    else:
        result = (
            f"⚠️ **Не удалось выполнить задачу за {MAX_ITERATIONS} итераций.**\n\n"
            f"Сессия: `{session_dir}`\n"
            f"План: {shared['plan_path']}\n\n"
            f"Отчёт Orchestrator:\n{orch_result[:600]}\n\n"
            f"{stats}"
            f"{docs_report}"
        )

    if progress:
        await progress(
            "🏁 Orchestrator",
            f"Пайплайн завершён. Итераций: {shared['iterations']}, тулов: {sum(tool_counter.values())}",
        )

    return result

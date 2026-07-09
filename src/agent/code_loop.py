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
from src.agent.logger import (
    log_node_start,
    log_node_end,
    log_llm_call,
    log_tool_call,
    _now_ms as _log_now_ms,
)
from src.agent.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")

MAX_ITERATIONS = 5
MAX_KEPT_SESSIONS = 10
MAX_TOOL_ITERS = 50  # макс. вызовов тулов в одном агенте (шаги рассуждений)
MAX_ORCHESTRATOR_ITERS = 50  # макс. шагов рассуждений оркестратора

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
    session_id: str = "",
    node_name: str = "",
) -> str:
    """Выполняет LLM с инструментами в цикле (ReAct) до получения текстового ответа.

    Каждое исполнение тула записывается в tool_counter для итоговой статистики.
    """
    messages: list[BaseMessage] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]
    llm_with_tools = llm.bind_tools(tools)
    model_name = getattr(llm, "_model", "pro")

    for _ in range(max_iters):
        t0 = _log_now_ms()
        response = await llm_with_tools.ainvoke(messages)
        llm_ms = _log_now_ms() - t0

        messages.append(response)

        prompt_len = sum(len(str(m.content)) for m in messages[:-1])
        response_len = len(str(response.content))
        if session_id:
            log_llm_call(
                session_id, model_name, prompt_len, response_len, llm_ms, node=node_name
            )

        if not response.tool_calls:
            return str(response.content) if response.content else ""

        # Исполняем тулы, считаем.
        tool_results: list[ToolMessage] = []
        for tc in response.tool_calls:
            name = tc["name"]
            args = tc.get("args", {})
            tool_counter[name] += 1
            logger.debug("Tool call: %s(%s)", name, args)

            t1 = _log_now_ms()
            # Находим тул по имени.
            tool_fn = None
            for t in tools:
                if t.name == name:
                    tool_fn = t
                    break

            if tool_fn:
                try:
                    result = await tool_fn.ainvoke(args)
                    tool_status = "ok"
                except Exception as e:
                    result = f"Error executing {name}: {e}"
                    tool_status = "error"
            else:
                result = f"Unknown tool: {name}"
                tool_status = "error"

            tool_ms = _log_now_ms() - t1
            if session_id:
                log_tool_call(
                    session_id,
                    name,
                    str(args)[:200],
                    tool_status,
                    tool_ms,
                    node=node_name,
                    result_summary=str(result)[:200],
                )

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
    session_id: str = "",
) -> str:
    """Planner: изучает задачу, составляет план, сохраняет в plan.md."""
    node_name = "planner"
    start_ms = 0
    if session_id:
        start_ms = log_node_start(session_id, node_name, branch="code_task")

    llm = get_pro_llm()
    tc = tool_counter or Counter()

    system_prompt = load_prompt("agents/code/planner", task=task, session_dir=session_dir)
    user_prompt = (
        f"Задача: {task}\n\n"
        f"Директория сессии: {session_dir}\n"
        f"Составь план и сохрани его в {session_dir}/plan.md"
    )

    # Planner: read_file + write_file + list_directory + search_content
    from src.agent.tools import read_file, write_file, list_directory, search_content

    planner_tools = [read_file, write_file, list_directory, search_content]

    result = await _run_with_tools(
        llm,
        system_prompt,
        user_prompt,
        planner_tools,
        tc,
        session_id=session_id,
        node_name=node_name,
    )

    # Гарантированно сохраняем план.
    plan_path = os.path.join(session_dir, "plan.md")
    if not os.path.exists(plan_path) or os.path.getsize(plan_path) < 50:
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write(result)

    logger.info("Planner сохранил план: %s", plan_path)

    if session_id:
        log_node_end(
            session_id,
            node_name,
            start_ms,
            branch="code_task",
            data={"plan_path": plan_path},
        )
    return plan_path


async def run_developer(
    task: str,
    plan_path: str,
    feedback: Optional[str] = None,
    progress: Optional[ProgressCallback] = None,
    tool_counter: Optional[Counter] = None,
    session_id: str = "",
) -> str:
    """Developer: следует плану, вносит правки в код через ReAct-цикл."""
    node_name = "developer"
    start_ms = 0
    if session_id:
        start_ms = log_node_start(
            session_id,
            node_name,
            branch="code_task",
            data={"has_feedback": bool(feedback)},
        )

    llm = get_pro_llm()
    tc = tool_counter or Counter()

    plan_content = ""
    if os.path.exists(plan_path):
        with open(plan_path, "r", encoding="utf-8") as f:
            plan_content = f.read()

    feedback_section = ""
    if feedback:
        feedback_section = (
            f"\n\n⚠️ Тестировщик нашёл ошибки. Вот feedback:\n{feedback}\n"
            "Исправь код согласно этому feedback и попробуй снова."
        )
    system_prompt = load_prompt(
        "agents/code/developer",
        plan_content=plan_content,
        task=task,
        feedback_section=feedback_section,
    )

    user_prompt = "Приступай к реализации по плану."

    result = await _run_with_tools(
        llm,
        system_prompt,
        user_prompt,
        developer_tools,
        tc,
        session_id=session_id,
        node_name=node_name,
    )

    logger.info("Developer завершил: %s", result[:200])

    if session_id:
        log_node_end(
            session_id,
            node_name,
            start_ms,
            branch="code_task",
            data={"result_len": len(result)},
        )
    return result


async def run_checker(
    task: str,
    plan_path: str,
    progress: Optional[ProgressCallback] = None,
    tool_counter: Optional[Counter] = None,
    session_id: str = "",
) -> tuple[bool, str]:
    """Checker: составляет тесты, тестирует код, возвращает (success, feedback)."""
    node_name = "checker"
    start_ms = 0
    if session_id:
        start_ms = log_node_start(session_id, node_name, branch="code_task")

    llm = get_pro_llm()
    tc = tool_counter or Counter()

    plan_content = ""
    if os.path.exists(plan_path):
        with open(plan_path, "r", encoding="utf-8") as f:
            plan_content = f.read()

    system_prompt = load_prompt("agents/code/tester", plan_content=plan_content, task=task)
    user_prompt = "Проверь код и запусти тесты."

    result = await _run_with_tools(
        llm,
        system_prompt,
        user_prompt,
        developer_tools,
        tc,
        session_id=session_id,
        node_name=node_name,
    )

    passed = "TESTS_PASSED" in result
    if passed:
        logger.info("Checker: тесты прошли")
    else:
        logger.info("Checker: тесты упали")

    if session_id:
        log_node_end(
            session_id, node_name, start_ms, branch="code_task", data={"passed": passed}
        )
    return passed, ("" if passed else result)


def _format_tool_stats(tool_counter: Counter) -> str:
    """Форматирует статистику использованных тулов для ответа пользователю."""
    if not tool_counter:
        return ""
    lines = []
    for name, count in tool_counter.most_common():
        lines.append(f"  • {name}: ×{count}")
    return "🛠 **Использовано инструментов:**\n" + "\n".join(lines)


async def _generate_final_summary(
    task: str,
    orch_result: str,
    shared: dict,
    changed_files: list[str],
    docs_report: str,
    tool_counter: Counter,
    session_id: str = "",
) -> str:
    """Генерирует итоговое сообщение пользователю через отдельный LLM-вызов.

    LLM получает контекст всей выполненной работы (план, отчёты Developer/Checker,
    изменённые файлы) и формирует связный ответ на русском языке:
    - что было сделано
    - какие файлы изменены
    - результат тестов
    - доп. информация (docs sync итд)
    """
    plan_content = ""
    plan_path = shared.get("plan_path", "")
    if plan_path and os.path.exists(plan_path):
        with open(plan_path, "r", encoding="utf-8") as f:
            plan_content = f.read()

    system_prompt = load_prompt("agents/code/final_summary")

    user_prompt = (
        f"## Задача пользователя\n{task}\n\n"
        f"## План (plan.md)\n{plan_content[:1000]}\n\n"
        f"## Отчёт Orchestrator (из ReAct-цикла)\n{orch_result[:1500]}\n\n"
        f"## Изменённые файлы\n{chr(10).join(changed_files) if changed_files else 'не определены'}\n\n"
        f"## Итераций: {shared['iterations']}\n"
        f"## Тесты пройдены: {'✓ Да' if shared['success'] else '✗ Нет'}\n"
        f"## Документация: {docs_report[:300] if docs_report else '—'}\n\n"
        "Сформируй итоговый ответ пользователю:"
    )

    llm = get_pro_llm()
    try:
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ])
        final_text = str(response.content) if response.content else ""
    except Exception as e:
        logger.warning("Final summary LLM call failed: %s", e)
        final_text = ""

    # Статистика тулов — добавляется отдельно
    stats = _format_tool_stats(tool_counter)
    if stats:
        final_text += f"\n\n---\n{stats}"

    if not final_text.strip():
        # Fallback, если LLM не ответил
        status_icon = "✅" if shared["success"] else "⚠️"
        final_text = (
            f"{status_icon} **Задача: {task[:80]}**\n\n"
            f"Итераций: {shared['iterations']}\n"
            f"Изменённые файлы: {', '.join(changed_files) if changed_files else '—'}\n"
            f"Тесты: {'пройдены' if shared['success'] else 'не пройдены'}\n\n"
            f"Подробнее: {orch_result[:500]}"
        )
        if stats:
            final_text += f"\n\n{stats}"

    return final_text


async def run_orchestrator(
    task: str,
    progress: Optional[ProgressCallback] = None,
    session_id: str = "",
) -> str:
    """Оркестратор — LLM-агент, руководящий Planner/Developer/Checker.

    Делегирует выполнение в CodeEditorSkill. Сохранена для обратной
    совместимости — используйте CodeEditorSkill напрямую из
    src.agent.skills.code_editor.
    """
    if progress is None:
        progress = _active_progress

    session_dir = _create_session_dir()
    if not session_id:
        from src.agent.logger import get_session_id

        session_id = get_session_id() or ""
    _purge_old_sessions()

    tool_counter: Counter = Counter()

    from src.agent.skills.code_editor import CodeEditorSkill
    from src.agent.skills.base import SkillContext

    context = SkillContext(
        session_id=session_id,
        session_dir=session_dir,
        progress=progress,
        tool_counter=tool_counter,
    )
    skill = CodeEditorSkill()
    return await skill.execute(task, context)

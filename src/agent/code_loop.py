"""Агентный луп для задач модификации кода.

Архитектура:
  Orchestrator (оркестратор) координирует цикл:
    1. Planner    — изучает задачу, составляет план, сохраняет в sessions/{id}/plan.md
    2. Developer  — следует плану, вносит правки в код через tools
    3. Checker    — пишет тесты, тестирует исправленный код
    4. Если тесты упали → обратно к Developer (шаг 2), цикл повторяется
    5. Если тесты прошли → результат пользователю

Прямое редактирование кода через write_file/edit/bash в Q&A-ветке ЗАПРЕЩЕНО.
Исключение — только .md-документация.
Все правки кода идут исключительно через этот агентный луп.
"""

import os
import uuid as _uuid
import logging
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage

from src.agent.llm_router import get_pro_llm
from src.agent.tools import developer_tools

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")

MAX_ITERATIONS = 3  # максимальное число циклов Developer→Checker


def _create_session_dir() -> str:
    """Создаёт директорию сессии для plan.md и тестов."""
    session_id = _uuid.uuid4().hex[:12]
    session_dir = os.path.join(SESSIONS_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    return session_dir


async def run_planner(task: str, session_dir: str) -> str:
    """Planner: изучает задачу, составляет план, сохраняет в plan.md.

    Возвращает путь к plan.md.
    """
    llm = get_pro_llm()

    sys_msg = SystemMessage(
        content=(
            "Ты — Planner агент. Твоя задача — изучить запрос пользователя на модификацию кода, "
            "проанализировать архитектуру проекта (используй инструменты для чтения файлов), "
            "и составить детальный пошаговый план реализации.\n\n"
            "План должен включать:\n"
            "1. Анализ текущего состояния кода (какие файлы затронуты)\n"
            "2. Пошаговые действия для реализации\n"
            "3. Потенциальные риски и как их mitigate\n"
            "4. Критерии приёмки (когда задача считается выполненной)\n\n"
            "Сохраняй план в файл plan.md в директории сессии.\n"
            "Используй write_file для сохранения плана."
        )
    )
    user_msg = HumanMessage(
        content=f"Задача: {task}\n\nСессия: {session_dir}\n\nСоставь план и сохрани его в {session_dir}/plan.md"
    )

    # Planner использует read_file и write_file (только для plan.md)
    llm_with_tools = llm.bind_tools(developer_tools)
    response = await llm_with_tools.ainvoke([sys_msg, user_msg])
    plan_text = str(response.content)

    # Гарантированно сохраняем план, даже если LLM не вызвала write_file.
    plan_path = os.path.join(session_dir, "plan.md")
    if not os.path.exists(plan_path):
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write(plan_text)

    logger.info("Planner сохранил план: %s", plan_path)
    return plan_path


async def run_developer(
    task: str, plan_path: str, feedback: Optional[str] = None
) -> str:
    """Developer: следует плану, вносит правки в код через tools.

    Если есть feedback от Checker — исправляет ошибки.
    Возвращает отчёт о внесённых изменениях.
    """
    llm = get_pro_llm()

    plan_content = ""
    if os.path.exists(plan_path):
        with open(plan_path, "r", encoding="utf-8") as f:
            plan_content = f.read()

    base_prompt = (
        "Ты — Developer агент. Следуй плану из plan.md и вноси правки в код.\n"
        "Используй инструменты read_file, write_file, execute_bash_command, list_directory.\n"
        "После выполнения верни краткий отчёт о том, что было изменено.\n\n"
        f"План:\n{plan_content}\n\n"
        f"Задача: {task}\n"
    )

    if feedback:
        base_prompt += (
            f"\n\n⚠️ Тестировщик нашёл ошибки. Вот feedback:\n{feedback}\n"
            "Исправь код согласно этому feedback и попробуй снова."
        )

    sys_msg = SystemMessage(content=base_prompt)
    user_msg = HumanMessage(content="Приступай к реализации по плану.")

    llm_with_tools = llm.bind_tools(developer_tools)
    response = await llm_with_tools.ainvoke([sys_msg, user_msg])
    dev_report = str(response.content)

    logger.info("Developer завершил работу: %s", dev_report[:200])
    return dev_report


async def run_checker(task: str, plan_path: str) -> tuple[bool, str]:
    """Checker: составляет тесты, тестирует код, возвращает (success, feedback).

    success=True — тесты прошли.
    success=False — тесты упали, feedback содержит описание ошибок.
    """
    llm = get_pro_llm()

    plan_content = ""
    if os.path.exists(plan_path):
        with open(plan_path, "r", encoding="utf-8") as f:
            plan_content = f.read()

    sys_msg = SystemMessage(
        content=(
            "Ты — Checker агент (тестировщик). Твоя задача — проверить, что код "
            "был правильно изменён согласно плану.\n\n"
            "Алгоритм:\n"
            "1. Прочитай план из plan.md\n"
            "2. Прочитай изменённый код (используй read_file)\n"
            "3. Составь тесты для проверки критериев приёмки из плана\n"
            "4. Запусти тесты (используй execute_bash_command)\n"
            "5. Проанализируй результаты\n\n"
            "Если тесты прошли успешно — ответь ТОЛЬКО: TESTS_PASSED\n"
            "Если тесты упали — ответь TESTS_FAILED, затем подробно опиши ошибки.\n\n"
            f"План:\n{plan_content}\n\n"
            f"Задача: {task}"
        )
    )
    user_msg = HumanMessage(content="Проверь код и запусти тесты.")

    llm_with_tools = llm.bind_tools(developer_tools)
    response = await llm_with_tools.ainvoke([sys_msg, user_msg])
    result = str(response.content).strip()

    if "TESTS_PASSED" in result:
        logger.info("Checker: тесты прошли")
        return True, ""
    else:
        logger.info("Checker: тесты упали")
        return False, result


async def run_orchestrator(task: str) -> str:
    """Orchestrator: координирует Planner → Developer → Checker с циклом.

    Возвращает финальный отчёт для пользователя.
    """
    session_dir = _create_session_dir()
    logger.info("Orchestrator: сессия %s, задача: %s", session_dir, task[:200])

    # Шаг 1: Planner
    plan_path = await run_planner(task, session_dir)

    # Шаг 2-3: Developer → Checker (цикл)
    feedback: Optional[str] = None
    dev_report = ""
    checker_feedback = ""
    for iteration in range(1, MAX_ITERATIONS + 1):
        logger.info("Orchestrator: итерация %d", iteration)

        # Developer
        dev_report = await run_developer(task, plan_path, feedback)

        # Checker
        success, checker_feedback = await run_checker(task, plan_path)

        if success:
            return (
                f"✅ Задача выполнена успешно!\n\n"
                f"Сессия: {session_dir}\n"
                f"План: {plan_path}\n"
                f"Итераций: {iteration}\n\n"
                f"Отчёт разработчика:\n{dev_report[:500]}"
            )
        else:
            feedback = checker_feedback
            logger.warning("Orchestrator: итерация %d провалена, повтор", iteration)

    # Исчерпали лимит итераций
    return (
        f"⚠️ Не удалось выполнить задачу за {MAX_ITERATIONS} итераций.\n\n"
        f"Сессия: {session_dir}\n"
        f"План: {plan_path}\n\n"
        f"Последний отчёт разработчика:\n{dev_report[:500]}\n\n"
        f"Последний feedback тестировщика:\n{checker_feedback[:500]}"
    )

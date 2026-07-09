import logging

from langchain_core.tools import tool as tool_decorator

from src.agent.skills.base import BaseSkill, SkillContext
from src.agent.code_loop import (
    run_planner,
    run_developer,
    run_checker,
    _run_with_tools,
    _generate_final_summary,
    _create_session_dir,
    _purge_old_sessions,
    MAX_ORCHESTRATOR_ITERS,
)
from src.agent.llm_router import get_pro_llm
from src.agent.docs_sync import (
    capture_git_snapshot,
    get_task_changed_files,
    sync_documentation,
)
from src.agent.prompt_loader import load_prompt
from src.agent.logger import get_session_id, log_node_start, log_node_end

logger = logging.getLogger(__name__)


class CodeEditorSkill(BaseSkill):
    name = "code_editor"
    description = "Редактирование кода: планирование → разработка → тестирование"

    async def execute(self, task: str, context: SkillContext) -> str:
        progress = context.progress
        session_id = context.session_id
        session_dir = context.session_dir or _create_session_dir()
        tool_counter = context.tool_counter

        if not session_id:
            session_id = get_session_id() or ""

        _purge_old_sessions()

        shared = {"plan_path": "", "feedback": None, "success": False, "iterations": 0}

        if progress:
            await progress(
                "🚀 code_editor", f"задача: {task[:120]}"
            )

        logger.info(
            "CodeEditorSkill: сессия %s, задача: %s", session_dir, task[:200]
        )

        # ─── Sub-agent tools для Orchestrator ──────────────────────────────

        @tool_decorator
        async def call_planner() -> str:
            """Вызвать Planner агента: анализирует задачу, изучает код, составляет план в plan.md.
            Вызывай ПЕРВЫМ, до Developer и Checker."""
            if progress:
                await progress(
                    "📋 Planner →",
                    "анализирую код и составляю план",
                )
            plan_path = await run_planner(
                task, session_dir, progress, tool_counter, session_id
            )
            shared["plan_path"] = plan_path
            return f"План создан и сохранён: {plan_path}"

        @tool_decorator
        async def call_developer(feedback: str = "") -> str:
            """Вызвать Developer агента: вносит правки в код согласно плану.
            Аргумент feedback передай, если Checker нашёл ошибки (TESTS_FAILED).
            Вызывай ПОСЛЕ Planner и после каждого провала тестов."""
            shared["iterations"] += 1
            detail = (
                "← Planner, вношу правки"
                if not feedback
                else f"🔄 итерация {shared['iterations']}, исправляю: {feedback[:100]}"
            )
            if progress:
                await progress("👨‍💻 Developer", detail)
            report = await run_developer(
                task,
                shared["plan_path"],
                feedback or None,
                progress,
                tool_counter,
                session_id,
            )
            return report

        @tool_decorator
        async def call_checker() -> str:
            """Вызвать Checker агента: пишет тесты, тестирует код.
            Возвращает 'TESTS_PASSED' или 'TESTS_FAILED: ...'.
            Вызывай ПОСЛЕ Developer."""
            if progress:
                await progress("🧪 Checker →", "← Developer, пишу тесты")
            success, feedback = await run_checker(
                task, shared["plan_path"], progress, tool_counter, session_id
            )
            shared["feedback"] = feedback
            shared["success"] = success
            if progress:
                icon = "✅" if success else "❌"
                await progress("🧪 Checker", f"{icon} {'пройдены' if success else 'упали → Developer'}")
            if success:
                return "TESTS_PASSED"
            else:
                return f"TESTS_FAILED: {feedback}"

        orchestrator_tools = [call_planner, call_developer, call_checker]

        # ─── Git snapshot ДО ───────────────────────────────────────────────

        git_before = await capture_git_snapshot()

        # ─── Orchestrator LLM ReAct-цикл ────────────────────────────────────

        llm = get_pro_llm()
        system_prompt = load_prompt("agents/code/orchestrator")

        orch_result = await _run_with_tools(
            llm,
            system_prompt,
            f"Задача пользователя: {task}",
            orchestrator_tools,
            tool_counter,
            max_iters=MAX_ORCHESTRATOR_ITERS,
            session_id=session_id,
            node_name="orchestrator_loop",
        )

        # ─── Docs Sync ──────────────────────────────────────────────────────

        docs_report = ""
        changed_files: list[str] = []
        try:
            changed_files = await get_task_changed_files(git_before)
            if changed_files:
                sync_result = await sync_documentation(changed_files)
                if sync_result:
                    docs_report = sync_result[:400]
        except Exception as e:
            logger.warning("Docs sync failed: %s", e)

        final_response = await _generate_final_summary(
            task=task,
            orch_result=orch_result,
            shared=shared,
            changed_files=changed_files,
            docs_report=docs_report,
            tool_counter=tool_counter,
            session_id=session_id,
        )

        if progress:
            await progress(
                "🏁 code_editor",
                f"✅ итераций: {shared['iterations']}",
            )

        return final_response

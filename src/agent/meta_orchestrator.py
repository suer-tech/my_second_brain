import logging
import functools

from langchain_core.tools import StructuredTool

from src.agent.skills.registry import get_all_skills
from src.agent.skills.base import SkillContext, BaseSkill
from src.agent.code_loop import _run_with_tools, MAX_ORCHESTRATOR_ITERS
from src.agent.llm_router import get_pro_llm
from src.agent.prompt_loader import load_prompt
from src.agent.tools import read_file, write_file, search_content, list_directory, execute_bash_command
from src.agent.utils import read_user_profile, read_schema_catalog, read_all_memory

logger = logging.getLogger(__name__)


async def _run_skill(task: str, skill: BaseSkill, context: SkillContext) -> str:
    return await skill.execute(task, context)


class MetaOrchestrator:
    """Единый координатор, управляющий всеми зарегистрированными скиллами.

    Анализирует запрос пользователя и выбирает подходящий скилл
    (или отвечает напрямую с read-only инструментами).
    """

    async def run(self, task: str, context: SkillContext) -> str:
        tools = self._build_tools(context)
        system_prompt = self._build_prompt()

        llm = get_pro_llm()

        result = await _run_with_tools(
            llm,
            system_prompt,
            task,
            tools,
            context.tool_counter,
            max_iters=MAX_ORCHESTRATOR_ITERS,
            session_id=context.session_id,
            node_name="meta_orchestrator",
        )

        return result

    def _build_tools(self, context: SkillContext) -> list:
        """Собирает инструменты: скиллы из реестра + общие read-only."""
        tools = []

        for skill in get_all_skills().values():
            t = StructuredTool.from_function(
                func=lambda _: "",
                coroutine=functools.partial(_run_skill, skill=skill, context=context),
                name=skill.name,
                description=skill.description,
            )
            tools.append(t)

        tools += [read_file, write_file, search_content, list_directory, execute_bash_command]

        return tools

    def _build_prompt(self) -> str:
        """Формирует системный промпт: system.md (база) + coordinator.md (роль + скиллы)."""
        profile = read_user_profile()
        wiki_context = read_schema_catalog()
        memory_context = read_all_memory()

        system_part = load_prompt(
            "system/system",
            profile=profile,
            wiki_context=wiki_context,
            memory_context=memory_context,
        )

        skills_list = "\n".join(
            f"- **{name}**: {skill.description}"
            for name, skill in get_all_skills().items()
        )

        coordinator_part = load_prompt(
            "agents/coordinator",
            skills_list=skills_list,
            profile=profile,
            wiki_context=wiki_context,
            memory_context=memory_context,
        )

        return system_part + "\n\n" + coordinator_part

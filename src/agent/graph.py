"""LangGraph — точка входа для обработки сообщений.

Архитектура: один MetaOrchestrator узел, который через LLM + ReAct
выбирает и вызывает зарегистрированные скиллы. Классификатор не нужен —
LLM сам решает, какой инструмент применить.
"""

import logging
from typing import TypedDict, Optional

from langgraph.graph import StateGraph, END

from src.agent.meta_orchestrator import MetaOrchestrator
from src.agent.skills.base import SkillContext
from src.agent.logger import log_node_start, log_node_end, get_session_id

logger = logging.getLogger(__name__)

# Лимит рекурсии ReAct-цикла внутри MetaOrchestrator / скиллов.
REACT_RECURSION_LIMIT = 50

# Модульный глобал: chat_id текущего пользователя. Устанавливается из handlers.py.
_active_chat_id: Optional[int] = None


class AgentState(TypedDict):
    input_content: str
    final_response: Optional[str]
    error: Optional[str]


async def meta_orchestrator_node(state: AgentState) -> dict:
    """Единая точка входа: MetaOrchestrator выбирает и запускает скилл."""
    session_id = get_session_id() or "unknown"
    start_ms = log_node_start(session_id, "meta_orchestrator", branch="main")
    task = state["input_content"]

    try:
        from src.agent.code_loop import _active_progress

        context = SkillContext(
            session_id=session_id,
            progress=_active_progress,
        )
        orchestrator = MetaOrchestrator()
        result = await orchestrator.run(task, context)
    except Exception as e:
        logger.error("MetaOrchestrator failed: %s", e, exc_info=True)
        result = f"Произошла ошибка: {str(e)}"

    log_node_end(
        session_id, "meta_orchestrator", start_ms, branch="main",
        data={"response_len": len(result)},
    )
    return {"final_response": result}


def build_graph():
    workflow = StateGraph(AgentState)
    workflow.add_node("meta_orchestrator", meta_orchestrator_node)
    workflow.set_entry_point("meta_orchestrator")
    workflow.add_edge("meta_orchestrator", END)
    return workflow.compile()

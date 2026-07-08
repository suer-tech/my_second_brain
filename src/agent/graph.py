import json
import uuid as _uuid
from langgraph.graph import StateGraph, END
from typing import TypedDict, Optional, Literal, Annotated, Sequence
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from src.agent.llm_router import get_flash_llm, get_pro_llm
from src.agent.utils import (
    read_user_profile,
    read_all_wiki,
    read_all_memory,
    is_url,
    fetch_url_text,
    save_raw_file,
    save_wiki_file,
    update_memory,
)
from src.agent import schema as schema_manager
from src.agent.tools import qa_readonly_tools
from src.agent.code_loop import run_orchestrator

# Лимит рекурсии ReAct-цикла qa_pro <-> tools.
REACT_RECURSION_LIMIT = 10


class AgentState(TypedDict):
    input_content: str
    input_type: Optional[Literal["ingest", "qa", "code_task"]]
    user_profile: Optional[str]
    raw_data_path: Optional[str]
    source_url: Optional[str]
    extracted_summary: Optional[str]
    extracted_tags: Optional[str]
    extracted_goals: Optional[str]
    final_response: Optional[str]
    error: Optional[str]
    messages: Annotated[Sequence[BaseMessage], add_messages]


# Ключевые слова, указывающие на задачу модификации кода.
_CODE_TASK_TRIGGERS = [
    "добавь",
    "исправь",
    "поправь",
    "перепиши",
    "рефактор",
    "refactor",
    "создай проект",
    "создай файл",
    "измени код",
    "обнови код",
    "fix",
    "add feature",
    "implement",
    "разработай",
    "модифицируй",
]


def _heuristic_classify(content: str) -> Literal["ingest", "qa", "code_task"]:
    """Резервная классификация при недоступности LLM."""
    if is_url(content):
        return "ingest"
    content_lower = content.lower()
    if any(trigger in content_lower for trigger in _CODE_TASK_TRIGGERS):
        return "code_task"
    if len(content) > 2000 and "?" not in content:
        return "ingest"
    return "qa"


async def analyze_context_node(state: AgentState) -> dict:
    """Классифицирует намерение: INGEST / QA / CODE_TASK."""
    profile = read_user_profile()
    content = state["input_content"].strip()

    input_type: Literal["ingest", "qa", "code_task"] = _heuristic_classify(content)
    try:
        llm = get_flash_llm()
        sys_msg = SystemMessage(
            content=(
                "You are an intent classifier for a personal AI knowledge agent. "
                "Classify the user's input into one of THREE categories. Output EXACTLY one word:\n"
                "- INGEST: URL, article, research paper, or long document to save.\n"
                "- QA: question, command, or short conversational message.\n"
                "- CODE_TASK: request to modify, create, fix, or refactor code in the project."
            )
        )
        user_msg = HumanMessage(content=content[:4000])
        decision = str((await llm.ainvoke([sys_msg, user_msg])).content).strip().upper()
        if "INGEST" in decision:
            input_type = "ingest"
        elif "CODE" in decision or "TASK" in decision:
            input_type = "code_task"
        elif "QA" in decision:
            input_type = "qa"
    except Exception:
        pass

    return {
        "user_profile": profile,
        "input_type": input_type,
        "messages": [HumanMessage(content=content)],
    }


def route_intent(
    state: AgentState,
) -> Literal["save_raw", "update_memory", "orchestrator"]:
    if state["input_type"] == "ingest":
        return "save_raw"
    if state["input_type"] == "code_task":
        return "orchestrator"
    return "update_memory"


# ─── Ветка INGEST ───────────────────────────────────────────────────────────


async def save_raw_node(state: AgentState) -> dict:
    content = state["input_content"]
    source_url: Optional[str] = None

    if is_url(content):
        source_url = content
        text = await fetch_url_text(content)
        if not text:
            return {
                "final_response": "Не удалось загрузить или извлечь текст по ссылке. Проверь URL.",
                "error": "fetch_url_failed",
            }
    else:
        text = content

    path = save_raw_file(text)
    return {"raw_data_path": path, "input_content": text, "source_url": source_url}


async def extract_flash_node(state: AgentState) -> dict:
    if state.get("error") or state.get("final_response"):
        return {}

    llm = get_flash_llm()
    text = state["input_content"]
    profile = state["user_profile"]

    sys_msg = SystemMessage(
        content=(
            f"You are a fast extraction assistant. User profile:\n{profile}\n\n"
            "Extract the core facts and concepts from the text. Focus ONLY on NEW or USEFUL info. "
            "ALSO extract 3-7 keyword tags for cross-referencing.\n\n"
            "Respond as JSON with keys:\n"
            '{"summary": "...", "tags": ["tag1", "tag2", ...]}'
        )
    )
    user_msg = HumanMessage(content=text[:10000])

    response = await llm.ainvoke([sys_msg, user_msg])
    raw = str(response.content).strip()

    summary = raw
    tags: list = []
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

    return {"extracted_summary": summary, "extracted_tags": tags}


async def compile_pro_node(state: AgentState) -> dict:
    if state.get("error") or state.get("final_response"):
        return {}

    llm = get_pro_llm()
    summary = state["extracted_summary"] or ""
    profile = state["user_profile"]
    tags: list = list(state.get("extracted_tags") or [])

    related = schema_manager.find_related(tags, limit=5)
    related_context = ""
    if related:
        related_titles = [f"- {a['title']} ({a['wiki_file']})" for a in related]
        related_context = f"\n\nRelated existing articles in Wiki:\n" + "\n".join(
            related_titles
        )

    sys_msg = SystemMessage(
        content=(
            f"You are a Senior AI Architect building a Wiki for a user with this profile:\n{profile}\n"
            "Based on the extracted summary, create a comprehensive Markdown article. "
            "FORMAT: Start with a short Business Summary (value, cost, use-case), "
            "then Technical Architecture (Mermaid diagrams if applicable)."
            f"{related_context}"
        )
    )
    user_msg = HumanMessage(content=summary)

    response = await llm.ainvoke([sys_msg, user_msg])
    wiki_content = str(response.content)

    title = summary[:60].strip().split("\n")[0] or f"Article_{_uuid.uuid4().hex[:8]}"
    wiki_path = save_wiki_file(title, wiki_content)
    raw_path = state.get("raw_data_path") or ""
    source_url = state.get("source_url")
    one_line_summary = summary[:200].replace("\n", " ")

    article_id = schema_manager.add_article(
        raw_file=raw_path,
        wiki_file=wiki_path,
        title=title,
        tags=tags,
        summary=one_line_summary,
        source_url=source_url,
    )

    if related:
        links_md = "\n\n## Связанные материалы\n"
        for a in related:
            links_md += f"- [[{a['wiki_file']}]] {a['title']}\n"
        with open(wiki_path, "a", encoding="utf-8") as f:
            f.write(links_md)

    return {
        "final_response": (
            f"Статья сохранена в Wiki: {wiki_path}\n"
            f"ID в схеме: {article_id}\n"
            f"Теги: {', '.join(tags) if tags else 'нет'}\n"
            f"Связанных статей: {len(related)}\n\n"
            f"Краткое ревью:\n{wiki_content[:500]}..."
        )
    }


# ─── Ветка QA ──────────────────────────────────────────────────────────────


async def update_memory_node(state: AgentState) -> dict:
    """Анализирует сообщение, извлекает личные данные и категоризует в memory/."""
    llm = get_flash_llm()
    question = state["input_content"]

    sys_msg = SystemMessage(
        content=(
            "You are a Memory Extraction Assistant. Analyze the user's message for personal info to remember. "
            "Categories: facts, preferences, people, projects.\n"
            "- facts: world knowledge, concepts the user cares about\n"
            "- preferences: tastes, habits, style\n"
            "- people: colleagues, friends, mentors, contacts\n"
            "- projects: current/past/planned projects, tech stack, status\n\n"
            'Respond as JSON: {"category": "facts|preferences|people|projects", "content": "bullet points to remember"}\n'
            'If nothing worth remembering, respond: {"category": null, "content": null}'
        )
    )
    user_msg = HumanMessage(content=question)

    response = await llm.ainvoke([sys_msg, user_msg])
    raw = str(response.content).strip()

    category: Optional[str] = None
    extracted: Optional[str] = None
    try:
        json_str = raw
        if "```" in raw:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = raw[start:end]
        parsed = json.loads(json_str)
        category = parsed.get("category")
        extracted = parsed.get("content")
    except (json.JSONDecodeError, ValueError):
        pass

    if category and extracted:
        update_memory(category, extracted)

    return {"extracted_goals": extracted if extracted else None}


async def qa_pro_node(state: AgentState) -> dict:
    """Q&A с read-only tools. Прямое редактирование кода ЗАПРЕЩЕНО.

    Пользователь может задавать вопросы и получать ответы на основе Wiki,
    памяти и read-only доступа к файловой системе. Для правок кода
    используется отдельная ветка CODE_TASK (агентный луп).
    """
    llm = get_pro_llm().bind_tools(qa_readonly_tools)
    profile = state.get("user_profile") or ""
    wiki_context = read_all_wiki()
    memory_context = read_all_memory()
    goals = state.get("extracted_goals")

    sys_msg = SystemMessage(
        content=(
            "Ты — ИИ-ассистент и боевой товарищ пользователя. "
            "Общайся на «ты», как опытный коллега. Дружеский тон, но серьёзен в архитектуре.\n\n"
            f"User profile:\n{profile}\n\n"
            f"Wiki Knowledge Base:\n{wiki_context}\n\n"
            f"Personal Memory (копия пользователя):\n{memory_context}\n\n"
            "У тебя есть read-only доступ к файловой системе (read_file, list_directory). "
            "ВНИМАНИЕ: Прямое редактирование кода через write_file/edit/bash ЗАПРЕЩЕНО. "
            "Если пользователь просит внести правки в код — скажи ему, что для этого "
            "нужно отправить запрос на модификацию, и он будет обработан через "
            "агентный луп (Planner → Developer → Checker). "
            "Исключение — только .md-документация."
        )
    )

    msgs = [sys_msg] + list(state.get("messages", []))
    response = await llm.ainvoke(msgs)

    final_text = str(response.content) if response.content else ""
    if final_text and goals:
        final_text += f"\n\n---\n*🧠 [Обновлена память]: Я запомнил:*\n{goals}"

    return {"messages": [response], "final_response": final_text}


# ─── Ветка CODE_TASK (агентный луп) ─────────────────────────────────────────


async def orchestrator_node(state: AgentState) -> dict:
    """Оркестратор: координирует Planner → Developer → Checker с циклом."""
    task = state["input_content"]
    result = await run_orchestrator(task)
    return {"final_response": result}


# ─── Сборка графа ───────────────────────────────────────────────────────────


def build_graph():
    workflow = StateGraph(AgentState)

    # Общий
    workflow.add_node("analyze_context", analyze_context_node)

    # Ветка INGEST
    workflow.add_node("save_raw", save_raw_node)
    workflow.add_node("extract_flash", extract_flash_node)
    workflow.add_node("compile_pro", compile_pro_node)

    # Ветка QA
    workflow.add_node("update_memory", update_memory_node)
    workflow.add_node("qa_pro", qa_pro_node)
    workflow.add_node("tools", ToolNode(qa_readonly_tools))

    # Ветка CODE_TASK
    workflow.add_node("orchestrator", orchestrator_node)

    workflow.set_entry_point("analyze_context")
    workflow.add_conditional_edges("analyze_context", route_intent)

    # INGEST: save_raw → extract_flash → compile_pro → END
    workflow.add_edge("save_raw", "extract_flash")
    workflow.add_edge("extract_flash", "compile_pro")
    workflow.add_edge("compile_pro", END)

    # QA: update_memory → qa_pro ↔ tools → END
    workflow.add_edge("update_memory", "qa_pro")
    workflow.add_conditional_edges("qa_pro", tools_condition)
    workflow.add_edge("tools", "qa_pro")

    # CODE_TASK: orchestrator → END
    workflow.add_edge("orchestrator", END)

    return workflow.compile()

import operator
from langgraph.graph import StateGraph, END
from typing import TypedDict, Optional, Literal, Annotated, Sequence
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from src.agent.llm_router import get_flash_llm, get_pro_llm
from src.agent.utils import (
    read_user_profile, 
    read_all_wiki, 
    is_url, 
    fetch_url_text, 
    save_raw_file, 
    save_wiki_file,
    update_user_profile
)
from src.agent.tools import developer_tools

class AgentState(TypedDict):
    input_content: str
    input_type: Optional[Literal["ingest", "qa"]]
    user_profile: Optional[str]
    raw_data_path: Optional[str]
    extracted_summary: Optional[str]
    extracted_goals: Optional[str]
    final_response: Optional[str]
    error: Optional[str]
    messages: Annotated[Sequence[BaseMessage], add_messages]

def analyze_context_node(state: AgentState) -> dict:
    """Reads profile, classifies intent (URL/article vs Question/Command)."""
    profile = read_user_profile()
    content = state["input_content"].strip()
    
    if is_url(content) or (len(content) > 500 and "?" not in content[-100:]):
        input_type = "ingest"
    else:
        input_type = "qa"
        
    return {
        "user_profile": profile,
        "input_type": input_type,
        "messages": [HumanMessage(content=content)]
    }

def route_intent(state: AgentState) -> Literal["save_raw", "update_memory"]:
    if state["input_type"] == "ingest":
        return "save_raw"
    return "update_memory"

def save_raw_node(state: AgentState) -> dict:
    content = state["input_content"]
    if is_url(content):
        text = fetch_url_text(content)
    else:
        text = content
        
    path = save_raw_file(text)
    return {"raw_data_path": path, "input_content": text} 

def extract_flash_node(state: AgentState) -> dict:
    llm = get_flash_llm()
    text = state["input_content"]
    profile = state["user_profile"]
    
    sys_msg = SystemMessage(content=(
        f"You are a fast extraction assistant. Read the following user profile:\n{profile}\n\n"
        "Extract the core facts, concepts, and technical details from the provided text. "
        "Focus ONLY on things that would be NEW or USEFUL to the user based on their profile. "
        "Do not explain basic concepts they already know."
    ))
    user_msg = HumanMessage(content=text[:10000])
    
    response = llm.invoke([sys_msg, user_msg])
    return {"extracted_summary": response.content}

def compile_pro_node(state: AgentState) -> dict:
    llm = get_pro_llm()
    summary = state["extracted_summary"]
    profile = state["user_profile"]
    
    sys_msg = SystemMessage(content=(
        f"You are a Senior AI Architect building a Wiki for a user with this profile:\n{profile}\n\n"
        "Based on the extracted summary, create a comprehensive Markdown article. "
        "FORMAT REQUIREMENT: Start with a short Business Summary (value, cost, use-case), "
        "then provide the Technical Architecture (use Mermaid diagrams if applicable)."
    ))
    user_msg = HumanMessage(content=summary)
    
    response = llm.invoke([sys_msg, user_msg])
    wiki_content = response.content
    
    title = f"Article_{hash(summary) % 10000}"
    path = save_wiki_file(title, wiki_content)
    
    return {"final_response": f"Статья обработана и сохранена в Wiki: {path}\n\nКраткое ревью:\n{wiki_content[:500]}..."}

def update_memory_node(state: AgentState) -> dict:
    """Analyzes user message for long-term goals or intents and updates memory if needed."""
    llm = get_flash_llm()
    question = state["input_content"]
    
    sys_msg = SystemMessage(content=(
        "You are a Memory Extraction Assistant. Analyze the user's message. "
        "Does it contain any new long-term career goals, learning plans, or information about their past/current professional experience and competencies? "
        "If yes, extract them clearly as bullet points. "
        "If no, output exactly the word 'EMPTY' (without quotes)."
    ))
    user_msg = HumanMessage(content=question)
    
    response = llm.invoke([sys_msg, user_msg])
    extracted = response.content.strip()
    
    if extracted != "EMPTY" and "EMPTY" not in extracted:
        update_user_profile(extracted)
        fresh_profile = read_user_profile()
        return {"extracted_goals": extracted, "user_profile": fresh_profile}
        
    return {"extracted_goals": None}

def qa_pro_node(state: AgentState) -> dict:
    # Привязываем системные инструменты (терминал, файлы) к LLM
    llm = get_pro_llm().bind_tools(developer_tools)
    profile = state.get("user_profile", "")
    wiki_context = read_all_wiki()
    goals = state.get("extracted_goals")
    
    sys_msg = SystemMessage(content=(
        "Ты — Autonomous AI Developer и боевой товарищ пользователя. "
        "Общайся с пользователем на «ты», как опытный коллега. Используй дружеский, неформальный тон (bro-style, но профессионально). "
        "Иногда можешь пошутить или сыронизировать над кодом, но когда дело касается архитектуры проектов и девопса — "
        "включай режим «душного синьора» и относись к задачам максимально серьезно.\n\n"
        f"User profile (Твои знания о пользователе):\n{profile}\n\n"
        f"Current Wiki Knowledge Base Context:\n{wiki_context}\n\n"
        "У тебя есть доступ к терминалу и файловой системе (через tools). "
        "Если пользователь просит создать проект или запустить код — ДЕЛАЙ ЭТО сам с помощью тулзов, а не просто пиши текст. "
        "Внимательно читай логи ошибок, если команда падает, и исправляй их как настоящий DevOps."
    ))
    
    # Собираем историю сообщений для ReAct цикла
    msgs = [sys_msg] + list(state.get("messages", []))
    
    response = llm.invoke(msgs)
    
    final_text = response.content if response.content else ""
    if final_text and goals:
        final_text += f"\n\n---\n*🧠 [Обновлена память]: Я запомнил твои новые планы/цели:*\n{goals}"
        
    return {"messages": [response], "final_response": final_text}

def build_graph():
    workflow = StateGraph(AgentState)

    workflow.add_node("analyze_context", analyze_context_node)
    
    # Ingestion branch
    workflow.add_node("save_raw", save_raw_node)
    workflow.add_node("extract_flash", extract_flash_node)
    workflow.add_node("compile_pro", compile_pro_node)
    
    # QA / Coding branch with memory update
    workflow.add_node("update_memory", update_memory_node)
    workflow.add_node("qa_pro", qa_pro_node)
    
    # Tool node for executing code
    workflow.add_node("tools", ToolNode(developer_tools))

    workflow.set_entry_point("analyze_context")
    
    workflow.add_conditional_edges("analyze_context", route_intent)
    
    workflow.add_edge("save_raw", "extract_flash")
    workflow.add_edge("extract_flash", "compile_pro")
    workflow.add_edge("compile_pro", END)
    
    workflow.add_edge("update_memory", "qa_pro")
    
    # Conditional edge for tools
    workflow.add_conditional_edges("qa_pro", tools_condition)
    workflow.add_edge("tools", "qa_pro")

    return workflow.compile()

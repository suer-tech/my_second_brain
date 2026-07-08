"""Система структурированного логирования шагов графа.

Формат: JSONL (одно событие JSON на строку).
Файл: logs/{session_id}.jsonl
"""

import json
import os
import time
from typing import Any, Optional

LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "logs"
)

os.makedirs(LOG_DIR, exist_ok=True)

# Модульный глобал: session_id текущего выполнения. Устанавливается из handlers.py
# перед вызовом графа, аналогично _active_chat_id и _active_progress.
_active_session_id: Optional[str] = None


def _now_ms() -> int:
    return time.monotonic_ns() // 1_000_000


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def _log_event(
    session_id: str,
    event_type: str,
    *,
    node: str = "",
    branch: str = "",
    level: str = "INFO",
    duration_ms: int = 0,
    data: dict | None = None,
) -> None:
    event = {
        "timestamp": _timestamp(),
        "session_id": session_id,
        "event_type": event_type,
        "node": node,
        "branch": branch,
        "level": level,
        "duration_ms": duration_ms,
        "data": data or {},
    }
    filepath = os.path.join(LOG_DIR, f"{session_id}.jsonl")
    try:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass


def set_session_id(session_id: str) -> None:
    global _active_session_id
    _active_session_id = session_id


def get_session_id() -> Optional[str]:
    return _active_session_id


def log_node_start(
    session_id: str, node_name: str, branch: str = "", data: dict | None = None
) -> int:
    start_ms = _now_ms()
    _log_event(
        session_id,
        "node_start",
        node=node_name,
        branch=branch,
        data=data,
    )
    return start_ms


def log_node_end(
    session_id: str,
    node_name: str,
    start_ms: int,
    branch: str = "",
    status: str = "ok",
    data: dict | None = None,
) -> None:
    duration = _now_ms() - start_ms
    event_data = {"status": status, **(data or {})}
    _log_event(
        session_id,
        "node_end",
        node=node_name,
        branch=branch,
        duration_ms=duration,
        data=event_data,
    )


def log_llm_call(
    session_id: str,
    model: str,
    prompt_len: int,
    response_len: int,
    duration_ms: int,
    node: str = "",
    level: str = "INFO",
) -> None:
    _log_event(
        session_id,
        "llm_call",
        node=node,
        level=level,
        duration_ms=duration_ms,
        data={
            "model": model,
            "prompt_chars": prompt_len,
            "response_chars": response_len,
        },
    )


def log_tool_call(
    session_id: str,
    tool_name: str,
    args_summary: str,
    status: str,
    duration_ms: int,
    node: str = "",
    result_summary: str = "",
) -> None:
    _log_event(
        session_id,
        "tool_call",
        node=node,
        duration_ms=duration_ms,
        data={
            "tool": tool_name,
            "args": args_summary,
            "status": status,
            "result": result_summary,
        },
    )


def log_classifier(
    session_id: str,
    input_preview: str,
    decision: str,
    heuristic: bool = False,
    duration_ms: int = 0,
) -> None:
    _log_event(
        session_id,
        "classifier",
        node="analyze_context",
        duration_ms=duration_ms,
        data={
            "input_preview": input_preview,
            "decision": decision,
            "heuristic": heuristic,
        },
    )


def log_routing(
    session_id: str,
    input_type: str,
    target_node: str,
) -> None:
    _log_event(
        session_id,
        "routing",
        node="route_intent",
        data={
            "input_type": input_type,
            "target": target_node,
        },
    )

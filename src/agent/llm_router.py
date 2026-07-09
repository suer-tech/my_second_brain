import os
import sys
import json
import asyncio
import logging
import subprocess

from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, SystemMessage

from src.agent.logger import log_llm_call, get_session_id, _now_ms as _log_now_ms

logger = logging.getLogger(__name__)

# ─── Провайдер 1: opencode CLI (ОСНОВНОЙ, бесплатный) ──────────────────────
OPENCODE_MODEL = "opencode/deepseek-v4-flash-free"
OPENCODE_TIMEOUT = 120  # секунд на один вызов subprocess

# ─── Провайдер 2: router_ai (РЕЗЕРВНЫЙ, при недоступности opencode) ────────
ROUTERAI_BASE_URL = "https://routerai.ru/api/v1"
ROUTERAI_MODEL = "deepseek/deepseek-v4-flash"


def _messages_to_prompt(messages: list[BaseMessage]) -> str:
    """Конвертирует список LangChain-сообщений в единый промпт для opencode CLI.

    Важно: промпт должен быть ОДНОЙ СТРОКОЙ (без \\n), т.к. на Windows cmd.exe
    в shell-режиме интерпретирует новые строки как разделители команд, и многострочный
    аргумент обрезается. Содержимое исходных сообщений с \\n схлопывается в пробелы.
    """
    parts: list[str] = []
    for msg in messages:
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        content = " ".join(content.splitlines())
        if isinstance(msg, SystemMessage):
            parts.append(f"[SYSTEM] {content}")
        elif isinstance(msg, HumanMessage):
            parts.append(f"[USER] {content}")
        else:
            parts.append(f"[ASSISTANT] {content}")
    return " | ".join(parts)


async def _call_opencode(messages: list[BaseMessage]) -> str:
    """Вызывает opencode CLI через subprocess, парсит JSONL-поток событий.

    Кросс-платформенный запуск: на Windows opencode — это .CMD-файл,
    требующий shell-mode; на Linux/Mac — прямой executable.
    Путь к бинарнику задаётся через OPENCODE_BIN (для VPS/Ubuntu).
    """
    prompt = _messages_to_prompt(messages)
    opencode_bin = os.getenv("OPENCODE_BIN", "opencode")
    args = [opencode_bin, "run", "-m", OPENCODE_MODEL, prompt, "--format", "json"]

    if sys.platform == "win32":
        # Windows: .CMD-файлы не запускаются через create_subprocess_exec.
        # list2cmdline корректно экранирует кавычки для cmd.exe.
        proc = await asyncio.create_subprocess_shell(
            subprocess.list2cmdline(args),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    stdout_bytes, stderr_bytes = await asyncio.wait_for(
        proc.communicate(), timeout=OPENCODE_TIMEOUT
    )

    if proc.returncode != 0:
        stderr = stderr_bytes.decode("utf-8", errors="ignore")
        raise RuntimeError(f"opencode CLI exited {proc.returncode}: {stderr[:500]}")

    # opencode --format json выдаёт поток событий (JSONL, одна строка — один JSON).
    stdout = stdout_bytes.decode("utf-8", errors="ignore")
    text_parts: list[str] = []
    for line in stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "text":
            text = event.get("part", {}).get("text", "")
            if text:
                text_parts.append(text)

    result = "".join(text_parts).strip()
    if not result:
        raise RuntimeError("opencode CLI вернул пустой ответ")
    return result


def _get_routerai_llm() -> ChatOpenAI:
    """Возвращает ChatOpenAI, настроенный на router_ai (OpenAI-совместимый API)."""
    return ChatOpenAI(
        model_name=ROUTERAI_MODEL,
        openai_api_key=os.getenv("ROUTERAI_API_KEY"),
        openai_api_base=ROUTERAI_BASE_URL,
    )


class UnifiedLLM:
    """
    Унифицированный LLM-провайдер с отказоустойчивостью.

    Стратегия:
      1. Простые вызовы (без tools): opencode CLI (бесплатный DeepSeek Flash).
         При ошибке/недоступности — откат на router_ai.
      2. Вызовы с tool-calling (bind_tools): всегда router_ai, т.к. opencode CLI
         через subprocess не поддерживает итеративный ReAct-цикл вызова инструментов.
    """

    def __init__(self, tools: list | None = None):
        self._tools = tools

    def bind_tools(self, tools: list):
        return UnifiedLLM(tools=tools)

    async def ainvoke(self, messages, **kwargs):
        messages = list(messages)
        session_id = get_session_id() or "unknown"
        t0 = _log_now_ms()

        prompt_len = sum(len(str(m.content)) for m in messages)

        # Tool-calling доступен только через router_ai (OpenAI-совместимый API).
        if self._tools:
            llm = _get_routerai_llm().bind_tools(self._tools)
            result = await llm.ainvoke(messages)
            response_len = len(str(result.content))
            log_llm_call(
                session_id,
                ROUTERAI_MODEL,
                prompt_len,
                response_len,
                _log_now_ms() - t0,
            )
            return result

        # Без инструментов: сначала opencode CLI.
        try:
            content = await _call_opencode(messages)
            response_len = len(content)
            log_llm_call(
                session_id,
                OPENCODE_MODEL,
                prompt_len,
                response_len,
                _log_now_ms() - t0,
            )
            return AIMessage(content=content)
        except Exception as e:
            logger.warning("opencode CLI недоступен, откат на router_ai: %s", e)

        # Fallback: router_ai.
        llm = _get_routerai_llm()
        result = await llm.ainvoke(messages)
        response_len = len(str(result.content))
        log_llm_call(
            session_id, ROUTERAI_MODEL, prompt_len, response_len, _log_now_ms() - t0
        )
        return result


def get_flash_llm() -> UnifiedLLM:
    """Возвращает унифицированный LLM для рутинных задач (opencode → router_ai)."""
    return UnifiedLLM()


def get_pro_llm() -> UnifiedLLM:
    """Возвращает унифицированный LLM для сложных задач и Q&A разработчика."""
    return UnifiedLLM()

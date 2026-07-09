# Agent Context

## Project

Telegram-бот «Второй мозг». ИИ-агент для управления знаниями: сохраняет статьи в Wiki, редактирует код, отвечает на вопросы.

## Tech Stack

- Python 3.12+, async
- aiogram 3.29+ (Telegram)
- LangGraph (оркестрация)
- LangChain (LLM, tools)
- LLM: opencode CLI (DeepSeek Flash) + router_ai API (DeepSeek Flash, tool-calling)
- Хранилище: файловое (raw/ wiki/ memory/ schema/)

## Architecture

```
User → Telegram → MetaOrchestrator (LLM + ReAct)
  ├── code_editor skill → Planner → Developer (diff) → Checker → Docs Sync
  ├── ingest skill → fetch → extract → compile → wiki
  └── direct answer → read/search/bash
```

- Единая точка входа: MetaOrchestrator
- Каждое новое поведение = новый скилл
- Никакого классификатора, никаких веток в графе
- Все изменения существующего кода — через `apply_patch` (diff)

## Key Files

| File | Purpose |
|---|---|
| `src/bot/handlers.py` | Telegram message handler, invokes graph |
| `src/agent/meta_orchestrator.py` | Main LLM agent, selects skills |
| `src/agent/graph.py` | LangGraph (1 node → END) |
| `src/agent/code_loop.py` | ReAct loop, planner/developer/checker |
| `src/agent/skills/` | Skill system + skills |
| `src/agent/tools.py` | Agent tools (read, write, patch, bash, search) |
| `src/agent/tools.py` | `developer_tools` used by CodeEditorSkill |
| `src/agent/llm_router.py` | UnifiedLLM (opencode + router_ai) |
| `prompts/` | All prompts in .md files |

## Rules

- Before changing code, check PROJECT_MAP.md to find the right files
- Before creating a new class, search for similar existing ones
- New skills → register in `src/agent/skills/__init__.py`
- New tools → add to `src/agent/tools.py`
- No global state (except `_active_progress` and `_active_chat_id` for single-user Telegram)
- All errors handled; none propagate to Telegram raw

# Project Map

## Entry Points

- `src/bot/main.py` — запуск Telegram бота
- `src/bot/handlers.py` — обработчики сообщений Telegram, точка входа в граф

## MetaOrchestrator

- `src/agent/meta_orchestrator.py` — MetaOrchestrator: LLM-агент, выбирает скилл
- `src/agent/graph.py` — LangGraph граф (единственный узел meta_orchestrator)
- `src/agent/code_loop.py` — `_run_with_tools` (ReAct-цикл), `run_planner`, `run_developer`, `run_checker`

## Skills

- `src/agent/skills/__init__.py` — авторегистрация при импорте
- `src/agent/skills/base.py` — `BaseSkill` (protocol), `SkillContext` (dataclass)
- `src/agent/skills/registry.py` — `register_skill()`, `get_skill()`, `get_all_skills()`
- `src/agent/skills/code_editor.py` — `CodeEditorSkill`: Planner → Developer → Checker
- `src/agent/skills/ingest.py` — `IngestSkill`: загрузка URL → извлечение → компиляция статьи

## Tools

- `src/agent/tools.py` — инструменты: `read_file`, `write_file`, `apply_patch`, `execute_bash_command`, `search_content`, `list_directory`

## LLM

- `src/agent/llm_router.py` — `UnifiedLLM`: opencode CLI (основной) + router_ai (fallback, tool-calling)
- `src/agent/prompt_loader.py` — загрузка промптов из `prompts/`

## Хранилище

- `src/agent/utils.py` — операции с `raw/`, `wiki/`, `memory/`, профиль пользователя
- `src/agent/schema.py` — менеджер `schema/index.json`: CRUD, перекрёстные ссылки
- `raw/` — сырые оригиналы статей
- `wiki/` — обработанные Markdown-статьи
- `memory/` — личное хранилище пользователя
- `schema/` — `index.json` (сопоставление raw↔wiki)

## Docs Sync

- `src/agent/docs_sync.py` — актуализация документации после правок кода

## Security

- `src/agent/security.py` — проверка bash-команд (Security Agent Interceptor)
- `prompts/system/safety.md` — safety-промпт для проверки команд

## Logging

- `src/agent/logger.py` — JSONL-логирование (LLM вызовы, tool calls, ноды графа)

## Configuration

- `.env` — секреты (BOT_TOKEN, ROUTERAI_API_KEY, ALLOWED_TELEGRAM_ID, OPENCODE_BIN)

## Prompts

- `prompts/agents/coordinator.md` — промпт MetaOrchestrator
- `prompts/agents/code/orchestrator.md` — промпт для CodeEditorSkill
- `prompts/agents/code/planner.md` — промпт Planner
- `prompts/agents/code/developer.md` — промпт Developer
- `prompts/agents/code/tester.md` — промпт Checker
- `prompts/agents/code/final_summary.md` — промпт для финального отчёта
- `prompts/agents/extract_flash.md` — промпт извлечения фактов (IngestSkill)
- `prompts/agents/compile_pro.md` — промпт компиляции статьи (IngestSkill)
- `prompts/agents/docs_sync.md` — промпт актуализации документации
- `prompts/system/system.md` — базовый системный промпт (личность, тон)

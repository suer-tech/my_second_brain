# Architecture

```
User → Telegram → handlers.py
  → graph.ainvoke()
    → MetaOrchestrator.run()
      → LLM + ReAct выбирает инструмент
        ├── code_editor → CodeEditorSkill
        │                 ├── call_planner → plan.md
        │                 ├── call_developer → правки (read → diff → apply_patch)
        │                 ├── call_checker → тесты
        │                 └── Docs Sync → docs/wiki/
        ├── ingest → IngestSkill
        │              ├── загрузка URL/text
        │              ├── extract_flash → факты + теги
        │              └── compile_pro → статья в wiki/
        └── (отвечает сам) → read_file / search / bash
```

## Компоненты

### MetaOrchestrator (`src/agent/meta_orchestrator.py`)
Единственная точка входа. LLM-агент с ReAct-циклом. Видит все зарегистрированные скиллы как инструменты + read/write/bash инструменты. Сам решает, какой скилл вызвать.

### LangGraph (`src/agent/graph.py`)
Один узел `meta_orchestrator` → END. Никакого классификатора, никаких веток. Всё решается внутри MetaOrchestrator через LLM.

### Skills (`src/agent/skills/`)
Плагинная архитектура. Каждый скилл — класс, реализующий `BaseSkill` (protocol):
- `name: str` — уникальное имя
- `description: str` — описание для LLM (видно при выборе инструмента)
- `async execute(task: str, context: SkillContext) -> str` — точка входа

Реестр (`registry.py`): `register_skill()`, `get_skill()`, `get_all_skills()`.
Авторегистрация в `__init__.py`.

### SkillContext
- `session_id` / `session_dir` — идентификатор и директория сессии
- `progress` — колбэк для Telegram-прогресса
- `tool_counter` — счётчик использованных инструментов

### CodeEditorSkill
Planner → Developer → Checker с циклом:
1. **Planner**: анализирует задачу, читает код, составляет план
2. **Developer**: вносит правки через read → diff → apply_patch
3. **Checker**: пишет тесты, запускает, проверяет
4. Если тесты упали → обратно к Developer (до 5 итераций)
5. Если прошли → Docs Sync → финальный отчёт

### IngestSkill
1. Определяет URL или текст
2. Загружает контент (aiohttp + trafilatura)
3. **Flash LLM**: извлекает факты + теги (JSON)
4. **Pro LLM**: компилирует Markdown-статью
5. Сохраняет в `wiki/`, регистрирует в `schema/index.json`

### Tools (`src/agent/tools.py`)
- `read_file(path)` — чтение файла
- `write_file(path, content)` — создание нового файла
- `apply_patch(diff)` — применение unified diff (безопасное редактирование)
- `execute_bash_command(command)` — выполнение bash (с проверкой безопасности)
- `search_content(pattern, path, include)` — поиск по содержимому
- `list_directory(path)` — список файлов в директории

### LLM (`src/agent/llm_router.py`)
Двухуровневая схема:
- **opencode CLI** — бесплатный DeepSeek Flash, для простых вызовов
- **router_ai** (OpenAI API) — fallback + tool-calling

### Хранилище
- `raw/` — сырые оригиналы статей (txt)
- `wiki/` — обработанные статьи (md)
- `memory/` — личная память пользователя
- `schema/index.json` — индекс сопоставления raw↔wiki

## Поток данных

1. Пользователь пишет в Telegram
2. `handlers.py` принимает, чистит прогресс, вызывает граф
3. `MetaOrchestrator` анализирует запрос + контекст (профиль, Wiki, память)
4. LLM решает: вызвать скилл или ответить самому
5. Если скилл — он выполняется, возвращает результат
6. Результат возвращается через граф → handlers → Telegram

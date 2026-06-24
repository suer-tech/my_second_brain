# Индексный файл (Граф знаний проекта)

Добро пожаловать в документацию проекта **«Второй мозг» (ИИ-Агент)**. 
Этот документ служит точкой входа (Root node) в граф документации.

## Граф связей проекта

```mermaid
graph TD
    Index[INDEX.md] --> Arch[ARCHITECTURE.md]
    Index --> Rules[AGENT_RULES.md]
    Arch --> Code[Исходный код /src]
    Rules --> Claude[CLAUDE.md]
```

## Основные разделы
- [Архитектура (ARCHITECTURE.md)](ARCHITECTURE.md) — Описание стека (aiogram, LangGraph, DeepSeek) и потока данных.
- [Правила разработки (AGENT_RULES.md)](AGENT_RULES.md) — Дополнительные правила для агентов при написании кода.
- [CLAUDE.md](../../CLAUDE.md) — Корневые жесткие правила системы (Writeback is Mandatory).

## Пользовательские директории
- `raw/` — сюда бот складывает входящие данные (парсинг YouTube, статьи).
- `wiki/` — сюда бот компилирует и структурирует знания в формате Markdown.
  - [Карта знаний пользователя (User Knowledge Map)](../../wiki/user_knowledge_map.md) — профиль компетенций для персонализации.

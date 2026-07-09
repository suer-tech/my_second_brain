# ADR 001: Skill System Architecture

## Decision

Replace the old classifier → 3-branch LangGraph architecture with a single MetaOrchestrator that manages registered skills.

## Context

The old approach used:
1. `analyze_context_node` — LLM-классификатор определял тип входящего сообщения
2. `route_intent` — условное ребро направляло в одну из трёх веток
3. Три независимые ветки: INGEST, QA, CODE_TASK

Проблемы:
- Классификатор ошибался, отправляя код-задачи в QA и наоборот
- Добавление новой функциональности требовало изменения графа (узлы, рёбра, промпты)
- Дублирование контекста (профиль, Wiki, память) между ветками
- 471 строка в graph.py

## Solution

- Каждое поведение — отдельный скилл, реализующий `BaseSkill` protocol
- Скиллы регистрируются в реестре и автоматически подхватываются MetaOrchestrator
- MetaOrchestrator — LLM-агент с ReAct-циклом, видит все скиллы как инструменты
- Единственный узел графа: `meta_orchestrator → END` (63 строки)

## Consequences

- Добавление скилла = создать класс + `register_skill()`, без изменения графа
- LLM сам решает, какой скилл вызвать — классификатор не нужен
- graph.py радикально упрощён
- Единый контекст (профиль, Wiki, память) для всех решений
- Риск: LLM может ошибиться в выборе скилла, но это лечится промптом

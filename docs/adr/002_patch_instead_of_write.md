# ADR 002: Patch-based Editing

## Decision

Use `apply_patch` (unified diff) instead of `write_file` for modifying existing files.

## Context

Developer agent used `read_file → edit content → write_file` cycle. Проблема:
- write_file перезаписывает весь файл, можно случайно удалить или изменить не ту часть
- LLM может сгенерировать некорректное содержимое, и файл будет потерян

## Solution

- Новый инструмент `apply_patch(diff)` — применяет unified diff через `git apply`
- Валидация: git проверяет, что контекст в diff совпадает с файлом
- Атомарность: или весь патч применён, или ничего
- `write_file` остаётся только для создания **новых** файлов
- Промпт Developer обновлён с инструкцией read → diff → apply_patch

## Consequences

- Безопасное редактирование существующего кода
- git apply отклоняет патчи с несовпадающим контекстом
- Для новых файлов — write_file (там нет риска перезаписи)
- Developer генерирует меньше токенов (только diff, не весь файл)

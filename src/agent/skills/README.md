# Skills

Система скиллов — плагинная архитектура для поведения агента.

## Как добавить новый скилл

```python
from src.agent.skills.base import BaseSkill, SkillContext

class MySkill(BaseSkill):
    name = "my_skill"
    description = "Описание для LLM (видно при выборе инструмента)"

    async def execute(self, task: str, context: SkillContext) -> str:
        # логика скилла
        return "результат"
```

Зарегистрировать в `src/agent/skills/__init__.py`:

```python
from src.agent.skills.registry import register_skill
register_skill(MySkill())
```

После регистрации MetaOrchestrator автоматически увидит скилл как доступный инструмент.

## Структура

- `base.py` — `BaseSkill` (protocol) + `SkillContext` (dataclass)
- `registry.py` — реестр скиллов
- `code_editor.py` — редактирование кода (Planner → Developer → Checker)
- `ingest.py` — сохранение статей/ссылок в Wiki

from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional, Protocol

ProgressCallback = Callable[[str, str], Awaitable[None]]


@dataclass
class SkillContext:
    session_id: str = ""
    session_dir: str = ""
    progress: Optional[ProgressCallback] = None
    tool_counter: Counter = field(default_factory=Counter)


class BaseSkill(Protocol):
    name: str
    description: str

    async def execute(self, task: str, context: SkillContext) -> str: ...

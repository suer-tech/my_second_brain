from typing import Optional
from src.agent.skills.base import BaseSkill

_skills: dict[str, BaseSkill] = {}


def register_skill(skill: BaseSkill) -> None:
    _skills[skill.name] = skill


def get_skill(name: str) -> Optional[BaseSkill]:
    return _skills.get(name)


def get_all_skills() -> dict[str, BaseSkill]:
    return dict(_skills)

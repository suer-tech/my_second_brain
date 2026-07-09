# Система скиллов — регистрация при импорте пакета

from src.agent.skills.base import BaseSkill, SkillContext
from src.agent.skills.registry import register_skill, get_skill, get_all_skills
from src.agent.skills.code_editor import CodeEditorSkill
from src.agent.skills.ingest import IngestSkill

# Авторегистрация встроенных скиллов
register_skill(CodeEditorSkill())
register_skill(IngestSkill())

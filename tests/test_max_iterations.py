"""
Тесты для проверки, что MAX_ITERATIONS установлен в 5 (увеличен с 3).
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent.code_loop import MAX_ITERATIONS


class TestMaxIterations(unittest.TestCase):
    """Проверка, что MAX_ITERATIONS = 5 (было 3)."""

    def test_max_iterations_is_5(self):
        """MAX_ITERATIONS из code_loop.py должен быть 5."""
        self.assertEqual(
            MAX_ITERATIONS,
            5,
            f"MAX_ITERATIONS = {MAX_ITERATIONS}, ожидается 5",
        )

    def test_max_iterations_is_not_3(self):
        """MAX_ITERATIONS не должен остаться 3 (старое значение)."""
        self.assertNotEqual(
            MAX_ITERATIONS,
            3,
            f"MAX_ITERATIONS = {MAX_ITERATIONS} — старое значение 3 не исправлено!",
        )

    def test_orchestrator_prompt_has_5(self):
        """Проверка, что в промпте оркестратора указано 5 итераций."""
        with open("prompts/agents/code/orchestrator.md", "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn(
            "5 полные итерации",
            content,
            "В промпте оркестратора не указано '5 полные итерации'",
        )
        self.assertNotIn(
            "3 полные итерации",
            content,
            "В промпте оркестратора осталось упоминание '3 полные итерации'",
        )


if __name__ == "__main__":
    unittest.main()

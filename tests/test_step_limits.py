"""
Тесты для проверки, что лимиты шагов рассуждений установлены в 50.
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent.graph import REACT_RECURSION_LIMIT
from src.agent.code_loop import MAX_TOOL_ITERS, MAX_ORCHESTRATOR_ITERS


class TestStepLimits(unittest.TestCase):
    """Проверка, что все лимиты шагов рассуждений установлены в 50."""

    def test_react_recursion_limit_is_50(self):
        """REACT_RECURSION_LIMIT из graph.py должен быть 50."""
        self.assertEqual(
            REACT_RECURSION_LIMIT,
            50,
            f"REACT_RECURSION_LIMIT = {REACT_RECURSION_LIMIT}, ожидается 50",
        )

    def test_max_tool_iters_is_50(self):
        """MAX_TOOL_ITERS из code_loop.py должен быть 50."""
        self.assertEqual(
            MAX_TOOL_ITERS,
            50,
            f"MAX_TOOL_ITERS = {MAX_TOOL_ITERS}, ожидается 50",
        )

    def test_max_orchestrator_iters_is_50(self):
        """MAX_ORCHESTRATOR_ITERS из code_loop.py должен быть 50."""
        self.assertEqual(
            MAX_ORCHESTRATOR_ITERS,
            50,
            f"MAX_ORCHESTRATOR_ITERS = {MAX_ORCHESTRATOR_ITERS}, ожидается 50",
        )

    def test_all_limits_are_integers(self):
        """Все лимиты должны быть целыми числами."""
        self.assertIsInstance(REACT_RECURSION_LIMIT, int)
        self.assertIsInstance(MAX_TOOL_ITERS, int)
        self.assertIsInstance(MAX_ORCHESTRATOR_ITERS, int)

    def test_recursion_limit_used_in_handlers(self):
        """Проверка, что REACT_RECURSION_LIMIT используется в handlers.py."""
        from src.bot.handlers import REACT_RECURSION_LIMIT as handler_limit
        self.assertEqual(handler_limit, 50)

    def test_no_old_small_limits_remain(self):
        """Проверка, что нет других констант с лимитами меньше 50."""
        # Проверяем, что нет старых значений (8, 10, 12 и т.д.)
        self.assertNotEqual(REACT_RECURSION_LIMIT, 10,
                            "Найдено старое значение 10 для REACT_RECURSION_LIMIT")
        self.assertNotEqual(MAX_TOOL_ITERS, 8,
                            "Найдено старое значение 8 для MAX_TOOL_ITERS")
        self.assertNotEqual(MAX_ORCHESTRATOR_ITERS, 12,
                            "Найдено старое значение 12 для MAX_ORCHESTRATOR_ITERS")


if __name__ == "__main__":
    unittest.main()

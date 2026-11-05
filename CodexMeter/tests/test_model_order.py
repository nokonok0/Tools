import unittest

from meter_ui import order_models


class ModelOrderTests(unittest.TestCase):
    def test_active_models_first_without_reordering_other_models(self):
        models = [('gpt-6.1-sol', 'low'), ('gpt-5.6-luna', 'high'), ('gpt-6.1-sol', 'high')]
        self.assertEqual(order_models(models, {models[1]}), [models[1], models[0], models[2]])

    def test_newer_generation_before_older(self):
        models = [('gpt-5.6-luna', 'high'), ('gpt-6.1-sol', 'high'), ('gpt-6-sol', 'low')]
        self.assertEqual(order_models(models), [models[1], models[2], models[0]])

    def test_groups_models_and_orders_effort_independent_of_recent_use(self):
        models = [('gpt-6.1-terra', 'high'), ('gpt-6.1-sol', 'high'),
                  ('gpt-6.1-sol', 'low'), ('gpt-6.1-sol', 'medium'),
                  ('gpt-6.1-terra', 'low')]
        self.assertEqual(order_models(models), [models[2], models[3], models[1], models[4], models[0]])

"""验证现有领域摘要的业务边界。"""
import copy
import json
import unittest
from pathlib import Path

from warning_metrics.analysis import residuals


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.tables = json.loads((Path(__file__).resolve().parents[1]/'fixtures/dataset.json').read_text())['tables']

    def test_unknown_is_not_replaced_by_zero(self):
        result = residuals(self.tables)
        unknown = next(row for row in result if row['constraint_id']=='missing')
        self.assertEqual('unknown',unknown['status'])
        self.assertIsNone(unknown['residual'])

    def test_different_units_cannot_be_balanced(self):
        tables = copy.deepcopy(self.tables)
        tables['cells'][0]['unit'] = 'household'
        with self.assertRaises(ValueError):
            residuals(tables)

    def test_local_residual_is_visible(self):
        row = next(row for row in residuals(self.tables) if row['constraint_id']=='sum')
        self.assertEqual(-5,row['residual'])
        self.assertEqual('conflict',row['status'])

    def test_declared_rounding_tolerance_is_applied(self):
        tables = copy.deepcopy(self.tables)
        tables['constraints'][0]['tolerance'] = 5
        self.assertEqual('balanced',residuals(tables)[0]['status'])

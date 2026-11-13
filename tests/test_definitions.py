import unittest

from warning_metrics.definitions import rate


class MetricTests(unittest.TestCase):
    def test_invalid_denominator_has_no_rate(self):
        self.assertIsNone(rate(1, 0))
        self.assertEqual(0.5, rate(1, 2))

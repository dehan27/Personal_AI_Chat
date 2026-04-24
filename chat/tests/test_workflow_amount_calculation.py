"""Phase 6-2 amount_calculation workflow 단위 테스트."""

from django.test import SimpleTestCase

from chat.workflows.core import WorkflowStatus, run_workflow
from chat.workflows.domains.general.amount_calculation import (
    AmountCalculationWorkflow,
    WORKFLOW_KEY,
)


class AmountCalculationTests(SimpleTestCase):
    def _run(self, raw):
        return run_workflow(AmountCalculationWorkflow(), raw)

    def test_sum_default_op(self):
        r = self._run({'values': [100, 200, 300]})
        self.assertEqual(r.status, WorkflowStatus.OK)
        self.assertEqual(r.value, 600)
        self.assertEqual(r.details['op'], 'sum')
        self.assertEqual(r.details['count'], 3)

    def test_sum_handles_strings_via_parse_int_like(self):
        r = self._run({'values': ['1,000', '2,000원', 3000]})
        self.assertEqual(r.value, 6000)

    def test_average_returns_float(self):
        r = self._run({'values': [100, 200, 350], 'op': 'average'})
        self.assertEqual(r.status, WorkflowStatus.OK)
        self.assertAlmostEqual(r.value, 216.6666666666, places=6)

    def test_diff_uses_max_minus_min(self):
        r = self._run({'values': [10, 50, 30], 'op': 'diff'})
        self.assertEqual(r.value, 40)

    def test_missing_values_returns_missing_input(self):
        r = self._run({'op': 'sum'})
        self.assertEqual(r.status, WorkflowStatus.MISSING_INPUT)
        self.assertIn('values', r.missing_fields)

    def test_diff_with_single_value_invalid(self):
        r = self._run({'values': [100], 'op': 'diff'})
        self.assertEqual(r.status, WorkflowStatus.INVALID_INPUT)
        self.assertTrue(any('2개 이상' in e for e in r.details['errors']))

    def test_unsupported_op_invalid_input(self):
        r = self._run({'values': [1, 2], 'op': 'median'})
        self.assertEqual(r.status, WorkflowStatus.INVALID_INPUT)
        self.assertTrue(any('op' in e for e in r.details['errors']))

    def test_non_numeric_value_invalid_input(self):
        r = self._run({'values': [1, 'oops']})
        self.assertEqual(r.status, WorkflowStatus.INVALID_INPUT)
        self.assertTrue(any('숫자' in e for e in r.details['errors']))

    def test_values_must_be_list(self):
        r = self._run({'values': 100})
        self.assertEqual(r.status, WorkflowStatus.INVALID_INPUT)

    def test_auto_registered_in_registry(self):
        from chat.workflows.domains import registry
        self.assertTrue(registry.has(WORKFLOW_KEY))
        entry = registry.get(WORKFLOW_KEY)
        self.assertEqual(entry.status, registry.STATUS_STABLE)
        self.assertIn('values', entry.input_schema)
        self.assertIn('op', entry.input_schema)

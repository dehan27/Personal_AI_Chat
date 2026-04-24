"""Phase 6-1 dispatch 단위 테스트."""

from django.test import SimpleTestCase

from chat.workflows.core import (
    ValidationResult,
    WorkflowResult,
    WorkflowStatus,
)
from chat.workflows.domains import dispatch, registry


class _AdderWorkflow:
    """a + b 를 돌려주는 최소 workflow (4 단계 계약 준수)."""
    def prepare(self, raw):
        return {'a': raw.get('a'), 'b': raw.get('b')}
    def validate(self, n):
        missing = [k for k in ('a', 'b') if n.get(k) is None]
        return ValidationResult.fail(missing=missing) if missing else ValidationResult.success()
    def execute(self, n):
        return WorkflowResult.ok(int(n['a']) + int(n['b']))


class DispatchTests(SimpleTestCase):
    def setUp(self):
        registry._reset_for_tests()
        registry.register(registry.WorkflowEntry(
            key='adder',
            title='Adder',
            description='test adder',
            status=registry.STATUS_STABLE,
            factory=lambda: _AdderWorkflow(),
        ))

    def tearDown(self):
        registry._reset_for_tests()

    def test_run_happy_path(self):
        result = dispatch.run('adder', {'a': 1, 'b': 2})
        self.assertEqual(result.status, WorkflowStatus.OK)
        self.assertEqual(result.value, 3)

    def test_run_missing_input_translated_via_run_workflow(self):
        # run_workflow(Phase 5) 가 ValidationResult.fail(missing=..) 을
        # MISSING_INPUT 으로 번역한다. dispatch 가 그걸 그대로 전달하는지 확인.
        result = dispatch.run('adder', {'a': 1})
        self.assertEqual(result.status, WorkflowStatus.MISSING_INPUT)
        self.assertEqual(result.missing_fields, ('b',))

    def test_run_unknown_key_returns_unsupported(self):
        result = dispatch.run('ghost', {})
        self.assertEqual(result.status, WorkflowStatus.UNSUPPORTED)
        self.assertIn('ghost', result.details['reason'])

    def test_run_empty_key_returns_unsupported(self):
        result = dispatch.run('', {})
        self.assertEqual(result.status, WorkflowStatus.UNSUPPORTED)
        self.assertIn('지정되지', result.details['reason'])

    def test_run_whitespace_key_treated_as_empty(self):
        result = dispatch.run('   ', {})
        self.assertEqual(result.status, WorkflowStatus.UNSUPPORTED)

"""Phase 6-1 registry 단위 테스트."""

from django.test import SimpleTestCase

from chat.workflows.core import (
    ValidationResult,
    WorkflowResult,
)
from chat.workflows.domains import registry


def _dummy_workflow_factory():
    class _Noop:
        def prepare(self, raw):
            return dict(raw)
        def validate(self, n):
            return ValidationResult.success()
        def execute(self, n):
            return WorkflowResult.ok(0)
    return _Noop()


class RegistryTests(SimpleTestCase):
    def setUp(self):
        registry._reset_for_tests()

    def tearDown(self):
        # 테스트 간 격리. 실제 부팅 시 등록되는 엔트리는 프로세스 종료까지 유지되지만
        # 단위 테스트에서는 매번 비워두고 써야 한다.
        registry._reset_for_tests()

    def test_register_and_lookup(self):
        entry = registry.WorkflowEntry(
            key='noop',
            title='Noop',
            description='doc',
            status=registry.STATUS_STABLE,
            factory=_dummy_workflow_factory,
        )
        registry.register(entry)

        self.assertTrue(registry.has('noop'))
        self.assertIs(registry.get('noop'), entry)
        self.assertEqual([e.key for e in registry.all_entries()], ['noop'])

    def test_unknown_key_returns_none(self):
        self.assertFalse(registry.has('ghost'))
        self.assertIsNone(registry.get('ghost'))

    def test_duplicate_key_rejected(self):
        entry = registry.WorkflowEntry(
            key='x', title='X', description='',
            status=registry.STATUS_STABLE, factory=_dummy_workflow_factory,
        )
        registry.register(entry)
        with self.assertRaises(ValueError):
            registry.register(entry)

    def test_empty_key_rejected(self):
        entry = registry.WorkflowEntry(
            key='', title='', description='',
            status=registry.STATUS_STABLE, factory=_dummy_workflow_factory,
        )
        with self.assertRaises(ValueError):
            registry.register(entry)

    def test_all_entries_preserves_insertion_order(self):
        for key in ('a', 'b', 'c'):
            registry.register(registry.WorkflowEntry(
                key=key, title=key.upper(), description='',
                status=registry.STATUS_STABLE,
                factory=_dummy_workflow_factory,
            ))
        self.assertEqual(
            [e.key for e in registry.all_entries()],
            ['a', 'b', 'c'],
        )

"""Phase 7-1 agent.tools 단위 테스트 — Tool / registry / call 분기."""

from django.test import SimpleTestCase

from chat.services.agent import tools
from chat.workflows.domains.field_spec import FieldSpec


def _schema_tool(callable_, *, name='echo', summarize=None):
    return tools.Tool(
        name=name,
        description='echoes query',
        input_schema={'query': FieldSpec(type='text', required=True)},
        callable=callable_,
        summarize=summarize or (lambda result: f'echoed: {result}'),
    )


def _raw_tool(callable_, *, name='raw_op', summarize=None):
    return tools.Tool(
        name=name,
        description='raw mode tool',
        input_schema=None,
        callable=callable_,
        summarize=summarize or (lambda result: f'raw: {result}'),
    )


class ToolsRegistryTests(SimpleTestCase):
    def setUp(self):
        self._snapshot = tools._snapshot_for_tests()
        tools._reset_for_tests()

    def tearDown(self):
        tools._restore_for_tests(self._snapshot)

    def test_register_and_lookup(self):
        t = _schema_tool(lambda args: args['query'])
        tools.register(t)
        self.assertTrue(tools.has('echo'))
        self.assertIs(tools.get('echo'), t)
        self.assertEqual([x.name for x in tools.all_entries()], ['echo'])

    def test_duplicate_name_rejected(self):
        t = _schema_tool(lambda args: '')
        tools.register(t)
        with self.assertRaises(ValueError):
            tools.register(t)

    def test_empty_name_rejected(self):
        t = tools.Tool(
            name='', description='', input_schema=None,
            callable=lambda args: None, summarize=lambda r: '',
        )
        with self.assertRaises(ValueError):
            tools.register(t)


class ToolsCallTests(SimpleTestCase):
    def setUp(self):
        self._snapshot = tools._snapshot_for_tests()
        tools._reset_for_tests()

    def tearDown(self):
        tools._restore_for_tests(self._snapshot)

    def test_unknown_tool_returns_failure_observation(self):
        obs = tools.call('ghost', {})
        self.assertTrue(obs.is_failure)
        self.assertIn('unknown tool', obs.summary)

    def test_schema_validation_failure_skips_callable(self):
        invoked = []
        tools.register(_schema_tool(lambda args: invoked.append(args)))
        obs = tools.call('echo', {})  # query 누락.
        self.assertTrue(obs.is_failure)
        self.assertIn('input invalid', obs.summary)
        self.assertIn('query', obs.summary)
        self.assertEqual(invoked, [])  # callable 호출 안 됨.

    def test_schema_mode_happy_path(self):
        tools.register(_schema_tool(lambda args: args['query'].upper()))
        obs = tools.call('echo', {'query': 'hello'})
        self.assertFalse(obs.is_failure)
        self.assertIn('HELLO', obs.summary)

    def test_raw_mode_skips_validation(self):
        # raw 모드는 query 누락 같은 검증 없이 callable 까지 도달.
        seen = []
        tools.register(_raw_tool(lambda args: seen.append(args) or 'ok'))
        obs = tools.call('raw_op', {'arbitrary': 'shape'})
        self.assertFalse(obs.is_failure)
        self.assertEqual(seen, [{'arbitrary': 'shape'}])

    def test_callable_exception_becomes_failure(self):
        def boom(args):
            raise RuntimeError('네트워크 실패')

        tools.register(_schema_tool(boom))
        obs = tools.call('echo', {'query': 'x'})
        self.assertTrue(obs.is_failure)
        self.assertIn('tool error', obs.summary)
        self.assertIn('네트워크 실패', obs.summary)

    def test_summarize_exception_keeps_success_but_notes_failure_to_summarize(self):
        def bad_summarize(result):
            raise ValueError('bad')

        tools.register(_schema_tool(
            lambda args: 'raw',
            summarize=bad_summarize,
        ))
        obs = tools.call('echo', {'query': 'x'})
        self.assertFalse(obs.is_failure)
        self.assertIn('summarize failed', obs.summary)

    def test_enum_value_outside_allowed_keys_fails(self):
        tools.register(tools.Tool(
            name='picker',
            description='enum test',
            input_schema={
                'op': FieldSpec(
                    type='enum', required=True,
                    enum_values={'sum': ('합',), 'avg': ('평균',)},
                ),
            },
            callable=lambda args: args['op'],
            summarize=lambda r: f'op={r}',
        ))
        obs = tools.call('picker', {'op': 'median'})
        self.assertTrue(obs.is_failure)
        self.assertIn('input invalid', obs.summary)

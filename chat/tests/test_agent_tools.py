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
        self.assertEqual(obs.failure_kind, 'schema_invalid')
        self.assertIn('input invalid', obs.summary)


# ---------------------------------------------------------------------------
# Phase 7-4: Tool.failure_check + tools.call failure_kind 세팅
# ---------------------------------------------------------------------------


class ToolsFailureKindTests(SimpleTestCase):
    """`Tool.failure_check` 와 `tools.call` 의 종류별 failure_kind 세팅 (Phase 7-4)."""

    def setUp(self):
        self._snapshot = tools._snapshot_for_tests()
        tools._reset_for_tests()

    def tearDown(self):
        tools._restore_for_tests(self._snapshot)

    def test_failure_check_none_means_always_success(self):
        # failure_check 미지정 → callable 정상 반환이면 is_failure=False, kind=None.
        tools.register(_raw_tool(lambda args: 'ok'))
        obs = tools.call('raw_op', {})
        self.assertFalse(obs.is_failure)
        self.assertIsNone(obs.failure_kind)

    def test_failure_check_true_marks_low_relevance(self):
        # failure_check True → is_failure=True, failure_kind='low_relevance'.
        tools.register(tools.Tool(
            name='lr_op',
            description='',
            input_schema=None,
            callable=lambda args: 'value',
            summarize=lambda r: f's:{r}',
            failure_check=lambda r: True,
        ))
        obs = tools.call('lr_op', {})
        self.assertTrue(obs.is_failure)
        self.assertEqual(obs.failure_kind, 'low_relevance')
        # callable 자체는 정상 — summary 는 보존.
        self.assertEqual(obs.summary, 's:value')

    def test_failure_check_exception_falls_back_to_not_failure(self):
        # P2-2: failure_check 자체 버그 시 자기충족적 spiral 방지 — not-failure 폴백.
        def boom_check(result):
            raise RuntimeError('checker bug')

        tools.register(tools.Tool(
            name='buggy_check',
            description='',
            input_schema=None,
            callable=lambda args: 'value',
            summarize=lambda r: f's:{r}',
            failure_check=boom_check,
        ))
        obs = tools.call('buggy_check', {})
        self.assertFalse(obs.is_failure)
        self.assertIsNone(obs.failure_kind)

    def test_callable_error_kind_distinct_from_low_relevance(self):
        # callable 예외 → failure_kind='callable_error', low_relevance 와 분리.
        def boom(args):
            raise RuntimeError('boom')

        tools.register(_raw_tool(boom))
        obs = tools.call('raw_op', {})
        self.assertTrue(obs.is_failure)
        self.assertEqual(obs.failure_kind, 'callable_error')


class ToolsUnknownToolKindTests(SimpleTestCase):
    """미등록 도구 호출 시 failure_kind='unknown_tool' (Phase 7-4)."""

    def setUp(self):
        self._snapshot = tools._snapshot_for_tests()
        tools._reset_for_tests()

    def tearDown(self):
        tools._restore_for_tests(self._snapshot)

    def test_unknown_tool_failure_kind(self):
        obs = tools.call('ghost', {})
        self.assertTrue(obs.is_failure)
        self.assertEqual(obs.failure_kind, 'unknown_tool')

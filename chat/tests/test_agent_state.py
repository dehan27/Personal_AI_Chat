"""Phase 7-1 AgentState / Observation / ToolCall 단위 테스트."""

from django.test import SimpleTestCase

from chat.services.agent.state import (
    MAX_OBSERVATION_SUMMARY_CHARS,
    AgentState,
    Observation,
    ToolCall,
)


class ObservationLengthTests(SimpleTestCase):
    def test_short_summary_passes_through(self):
        obs = Observation(tool='retrieve_documents', summary='3건')
        self.assertEqual(obs.summary, '3건')

    def test_long_summary_is_truncated_with_ellipsis(self):
        long = 'A' * (MAX_OBSERVATION_SUMMARY_CHARS + 50)
        obs = Observation(tool='t', summary=long)
        self.assertEqual(len(obs.summary), MAX_OBSERVATION_SUMMARY_CHARS)
        self.assertTrue(obs.summary.endswith('…'))

    def test_failure_flag_default_false(self):
        obs = Observation(tool='t', summary='ok')
        self.assertFalse(obs.is_failure)


class AgentStateTests(SimpleTestCase):
    def _state(self):
        return AgentState(question='2025-01-01 부터 며칠?', history=[])

    def test_defaults_clean(self):
        s = self._state()
        self.assertEqual(s.iteration_count, 0)
        self.assertEqual(s.observations, [])
        self.assertEqual(s.tool_calls, [])
        self.assertIsNone(s.final_answer)
        self.assertIsNone(s.termination)
        self.assertIsNone(s.error)

    def test_add_observation_appends_and_returns(self):
        s = self._state()
        obs = s.add_observation('retrieve_documents', '3건', is_failure=False)
        self.assertIs(s.observations[0], obs)
        self.assertEqual(obs.tool, 'retrieve_documents')

    def test_record_tool_call_freezes_arguments(self):
        s = self._state()
        args = {'query': '며칠'}
        call = s.record_tool_call('retrieve_documents', args)
        # 외부 dict 변경이 ToolCall.arguments 에 새지 않아야 한다.
        args['query'] = 'changed'
        self.assertEqual(call.arguments, {'query': '며칠'})

    def test_consecutive_failures_counts_from_tail(self):
        s = self._state()
        s.add_observation('t1', 'ok', is_failure=False)
        s.add_observation('t2', 'fail', is_failure=True)
        s.add_observation('t3', 'fail', is_failure=True)
        self.assertEqual(s.consecutive_failures(), 2)

    def test_consecutive_failures_zero_when_last_is_success(self):
        s = self._state()
        s.add_observation('t1', 'fail', is_failure=True)
        s.add_observation('t2', 'ok', is_failure=False)
        self.assertEqual(s.consecutive_failures(), 0)

    def test_repeated_call_count_matches_same_args_only(self):
        s = self._state()
        s.record_tool_call('retrieve_documents', {'query': 'A'})
        s.record_tool_call('retrieve_documents', {'query': 'A'})
        s.record_tool_call('retrieve_documents', {'query': 'B'})
        self.assertEqual(
            s.repeated_call_count('retrieve_documents', {'query': 'A'}),
            2,
        )
        self.assertEqual(
            s.repeated_call_count('retrieve_documents', {'query': 'B'}),
            1,
        )
        self.assertEqual(
            s.repeated_call_count('find_canonical_qa', {'query': 'A'}),
            0,
        )

    def test_repeated_call_count_is_order_independent(self):
        s = self._state()
        s.record_tool_call('run_workflow', {'workflow_key': 'date', 'input': {}})
        # 키 순서 다른 dict 도 같은 호출로 인식되어야 한다.
        self.assertEqual(
            s.repeated_call_count(
                'run_workflow', {'input': {}, 'workflow_key': 'date'},
            ),
            1,
        )

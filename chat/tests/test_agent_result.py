"""Phase 7-1 agent.result 단위 테스트 — termination → WorkflowResult 매핑.

Phase 8-1: AgentResult / SourceRef / ToolCallTrace / sources_as_dicts /
to_agent_result 추가 검증.
"""

from django.test import SimpleTestCase

from chat.services.agent.result import (
    AgentResult,
    AgentTermination,
    SourceRef,
    ToolCallTrace,
    to_agent_result,
    to_workflow_result,
)
from chat.services.agent.state import AgentState
from chat.workflows.core import BaseResult, WorkflowStatus


class ToWorkflowResultTests(SimpleTestCase):
    def test_final_answer_requires_value(self):
        with self.assertRaises(ValueError):
            to_workflow_result(AgentTermination.FINAL_ANSWER)

    def test_final_answer_returns_ok_with_value(self):
        r = to_workflow_result(AgentTermination.FINAL_ANSWER, value='42')
        self.assertEqual(r.status, WorkflowStatus.OK)
        self.assertEqual(r.value, '42')
        self.assertEqual(r.details['termination'], 'final_answer')

    def test_max_iterations_maps_to_not_found(self):
        # Phase 7-4: max_iter 도달은 "정리 못 함" 의미라 NOT_FOUND 가 맞음
        # (이전 UPSTREAM_ERROR 에서 변경 — broad query 시 재시도 무의미).
        r = to_workflow_result(AgentTermination.MAX_ITERATIONS_EXCEEDED)
        self.assertEqual(r.status, WorkflowStatus.NOT_FOUND)
        self.assertIn('더 구체적인 질문', r.details['reason'])

    def test_fatal_error_maps_to_upstream_error(self):
        # FATAL_ERROR (LLM/네트워크 일시 오류) 만 진짜 UPSTREAM_ERROR.
        r = to_workflow_result(AgentTermination.FATAL_ERROR)
        self.assertEqual(r.status, WorkflowStatus.UPSTREAM_ERROR)
        self.assertIn('일시적인 오류', r.details['reason'])

    def test_no_more_useful_tools_maps_to_not_found(self):
        r = to_workflow_result(AgentTermination.NO_MORE_USEFUL_TOOLS)
        self.assertEqual(r.status, WorkflowStatus.NOT_FOUND)

    def test_insufficient_evidence_maps_to_not_found(self):
        r = to_workflow_result(AgentTermination.INSUFFICIENT_EVIDENCE)
        self.assertEqual(r.status, WorkflowStatus.NOT_FOUND)

    def test_custom_reason_overrides_default(self):
        r = to_workflow_result(
            AgentTermination.INSUFFICIENT_EVIDENCE,
            reason='커스텀 사유',
        )
        self.assertEqual(r.details['reason'], '커스텀 사유')

    def test_agent_never_returns_unsupported(self):
        # UNSUPPORTED 는 라우팅 단의 책임이라 to_workflow_result 가 만들지 않는다.
        # enum 값에도 UNSUPPORTED 가 없는지 회귀 가드.
        self.assertNotIn('unsupported', {t.value for t in AgentTermination})

    def test_termination_values_are_plain_strings(self):
        # 직렬화·로그에 그대로 쓰일 수 있는 형태인지 확인.
        self.assertEqual(AgentTermination.FINAL_ANSWER.value, 'final_answer')
        self.assertEqual(
            AgentTermination.MAX_ITERATIONS_EXCEEDED.value,
            'max_iterations_exceeded',
        )


# ---------------------------------------------------------------------------
# Phase 8-1: AgentResult + SourceRef + ToolCallTrace + sources_as_dicts
# ---------------------------------------------------------------------------


class SourceRefTests(SimpleTestCase):
    def test_to_dict_returns_name_url_keys(self):
        ref = SourceRef(name='복리후생.pdf', url='/media/origin/x.pdf')
        self.assertEqual(
            ref.to_dict(),
            {'name': '복리후생.pdf', 'url': '/media/origin/x.pdf'},
        )


class AgentResultBasicsTests(SimpleTestCase):
    def test_satisfies_base_result_protocol(self):
        r = AgentResult(status=WorkflowStatus.OK, value='answer')
        self.assertIsInstance(r, BaseResult)

    def test_sources_as_dicts_empty(self):
        r = AgentResult(status=WorkflowStatus.OK, value='x')
        self.assertEqual(r.sources_as_dicts(), [])

    def test_sources_as_dicts_with_refs(self):
        refs = (
            SourceRef(name='a.pdf', url='/media/a'),
            SourceRef(name='b.pdf', url='/media/b'),
        )
        r = AgentResult(status=WorkflowStatus.OK, value='x', sources=refs)
        self.assertEqual(
            r.sources_as_dicts(),
            [{'name': 'a.pdf', 'url': '/media/a'}, {'name': 'b.pdf', 'url': '/media/b'}],
        )

    def test_to_workflow_result_adapter_for_ok(self):
        refs = (SourceRef(name='a', url='/media/a'),)
        traces = (
            ToolCallTrace(
                tool='retrieve_documents', arguments={'query': 'q'},
                is_failure=False, failure_kind=None, summary='ok',
            ),
        )
        r = AgentResult(
            status=WorkflowStatus.OK, value='answer', sources=refs,
            tool_calls=traces, termination=AgentTermination.FINAL_ANSWER,
        )
        wr = r.to_workflow_result()
        self.assertEqual(wr.status, WorkflowStatus.OK)
        self.assertEqual(wr.value, 'answer')
        # details 에 trace / sources 가 들어있어 외부 dict 분석에 쓸 수 있음.
        self.assertIn('tool_calls', wr.details)
        self.assertIn('sources', wr.details)


class ToAgentResultStateNoneTests(SimpleTestCase):
    """state=None 일 때 빈 trace / 빈 sources."""

    def test_final_answer_with_no_state(self):
        r = to_agent_result(AgentTermination.FINAL_ANSWER, value='ok')
        self.assertEqual(r.status, WorkflowStatus.OK)
        self.assertEqual(r.value, 'ok')
        self.assertEqual(r.tool_calls, ())
        self.assertEqual(r.sources, ())
        self.assertEqual(r.termination, AgentTermination.FINAL_ANSWER)

    def test_max_iter_with_no_state(self):
        r = to_agent_result(AgentTermination.MAX_ITERATIONS_EXCEEDED)
        self.assertEqual(r.status, WorkflowStatus.NOT_FOUND)
        self.assertIn('더 구체적인 질문', r.details['reason'])
        self.assertEqual(r.tool_calls, ())


class ToAgentResultWithStateTests(SimpleTestCase):
    """state 가 구성됐을 때 trace / sources 추출."""

    def _state_with_obs(self, *obs_specs):
        s = AgentState(question='Q', history=[])
        for spec in obs_specs:
            s.add_observation(**spec)
        return s

    def test_trace_from_observations_one_to_one(self):
        # 모든 obs 가 ToolCallTrace 로 1:1 매핑.
        s = self._state_with_obs(
            {'tool': 'retrieve_documents', 'summary': '3건', 'arguments': {'query': 'a'}},
            {'tool': 'retrieve_documents', 'summary': '4건', 'arguments': {'query': 'b'}},
            {'tool': '_llm', 'summary': 'invalid JSON', 'is_failure': True},
        )
        r = to_agent_result(AgentTermination.FINAL_ANSWER, value='x', state=s)
        self.assertEqual(len(r.tool_calls), 3)
        self.assertEqual(r.tool_calls[0].tool, 'retrieve_documents')
        self.assertEqual(r.tool_calls[0].arguments, {'query': 'a'})
        self.assertEqual(r.tool_calls[2].tool, '_llm')
        self.assertTrue(r.tool_calls[2].is_failure)

    def test_sources_collected_from_evidence_dedup(self):
        ref_a = SourceRef(name='a.pdf', url='/media/a')
        ref_a_dup = SourceRef(name='a.pdf', url='/media/a')
        ref_b = SourceRef(name='b.pdf', url='/media/b')
        s = self._state_with_obs(
            {'tool': 'retrieve_documents', 'summary': 's1', 'evidence': (ref_a,)},
            {'tool': 'retrieve_documents', 'summary': 's2', 'evidence': (ref_a_dup,)},
            {'tool': 'retrieve_documents', 'summary': 's3', 'evidence': (ref_b,)},
        )
        r = to_agent_result(AgentTermination.FINAL_ANSWER, value='x', state=s)
        # dedup → ref_a, ref_b 두 건만.
        self.assertEqual(len(r.sources), 2)
        self.assertEqual(r.sources[0].name, 'a.pdf')
        self.assertEqual(r.sources[1].name, 'b.pdf')

    def test_low_relevance_evidence_excluded_from_sources(self):
        ref_low = SourceRef(name='무관.pdf', url='/media/none')
        ref_ok = SourceRef(name='경조사.pdf', url='/media/x')
        s = self._state_with_obs(
            {'tool': 'retrieve_documents', 'summary': 'no rel',
             'is_failure': True, 'failure_kind': 'low_relevance',
             'evidence': (ref_low,)},
            {'tool': 'retrieve_documents', 'summary': 'ok',
             'evidence': (ref_ok,)},
        )
        r = to_agent_result(AgentTermination.FINAL_ANSWER, value='x', state=s)
        self.assertEqual(r.sources, (ref_ok,))

"""Phase 7-1 agent.result 단위 테스트 — termination → WorkflowResult 매핑."""

from django.test import SimpleTestCase

from chat.services.agent.result import AgentTermination, to_workflow_result
from chat.workflows.core import WorkflowStatus


class ToWorkflowResultTests(SimpleTestCase):
    def test_final_answer_requires_value(self):
        with self.assertRaises(ValueError):
            to_workflow_result(AgentTermination.FINAL_ANSWER)

    def test_final_answer_returns_ok_with_value(self):
        r = to_workflow_result(AgentTermination.FINAL_ANSWER, value='42')
        self.assertEqual(r.status, WorkflowStatus.OK)
        self.assertEqual(r.value, '42')
        self.assertEqual(r.details['termination'], 'final_answer')

    def test_max_iterations_maps_to_upstream_error(self):
        r = to_workflow_result(AgentTermination.MAX_ITERATIONS_EXCEEDED)
        self.assertEqual(r.status, WorkflowStatus.UPSTREAM_ERROR)
        self.assertIn('잠시 후 다시', r.details['reason'])

    def test_fatal_error_maps_to_upstream_error(self):
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

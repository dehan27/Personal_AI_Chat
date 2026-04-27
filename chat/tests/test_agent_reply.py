"""Phase 7-2 agent reply 포맷터 단위 테스트.

agent 가 만들 수 있는 세 status (OK / NOT_FOUND / UPSTREAM_ERROR) 의 카피
변환 + agent 가 만들지 않아야 하는 status (MISSING_INPUT / INVALID_INPUT /
UNSUPPORTED) 가 ValueError 로 가시화되는지 (fail-fast invariant) 확인.
"""

from django.test import SimpleTestCase

from chat.services.agent.reply import build_reply_from_agent_result
from chat.workflows.core import WorkflowResult


class AgentReplyTests(SimpleTestCase):
    # ---------- OK ----------
    def test_ok_returns_value_as_string(self):
        result = WorkflowResult.ok(
            value='자료에 따르면 본인 상 경조금은 500만원입니다.',
            details={'termination': 'final_answer'},
        )
        self.assertEqual(
            build_reply_from_agent_result(result),
            '자료에 따르면 본인 상 경조금은 500만원입니다.',
        )

    def test_ok_with_none_value_returns_empty_string(self):
        # 비정상 — agent 가 OK 인데 value None 으로 만들면 안 되지만,
        # reply 는 빈 문자열로 떨어져 사용자 화면이 깨지지 않게 한다.
        result = WorkflowResult(
            status=WorkflowResult.ok(value='').status,
            value=None,
        )
        self.assertEqual(build_reply_from_agent_result(result), '')

    # ---------- NOT_FOUND ----------
    def test_not_found_with_reason_passes_through(self):
        result = WorkflowResult.not_found('근거가 부족해 답을 만들기 어려웠습니다.')
        self.assertEqual(
            build_reply_from_agent_result(result),
            '근거가 부족해 답을 만들기 어려웠습니다.',
        )

    def test_not_found_without_reason_uses_default_copy(self):
        # 외부에서 직접 details 를 비운 NOT_FOUND 를 만들었다고 가정.
        result = WorkflowResult(status=WorkflowResult.not_found('').status)
        reply = build_reply_from_agent_result(result)
        self.assertIn('자료', reply)
        self.assertNotEqual(reply, '')

    # ---------- UPSTREAM_ERROR ----------
    def test_upstream_error_with_reason_passes_through(self):
        result = WorkflowResult.upstream_error('도구를 너무 많이 사용했어요.')
        self.assertEqual(
            build_reply_from_agent_result(result),
            '도구를 너무 많이 사용했어요.',
        )

    def test_upstream_error_without_reason_uses_default_copy(self):
        result = WorkflowResult(status=WorkflowResult.upstream_error('').status)
        reply = build_reply_from_agent_result(result)
        self.assertIn('일시적인 오류', reply)

    # ---------- 도달 불가 status — fail-fast invariant ----------
    def test_missing_input_raises_value_error(self):
        result = WorkflowResult.missing_input(['start', 'end'])
        with self.assertRaises(ValueError) as ctx:
            build_reply_from_agent_result(result)
        self.assertIn('missing_input', str(ctx.exception))

    def test_invalid_input_raises_value_error(self):
        result = WorkflowResult.invalid_input(['date format wrong'])
        with self.assertRaises(ValueError) as ctx:
            build_reply_from_agent_result(result)
        self.assertIn('invalid_input', str(ctx.exception))

    def test_unsupported_raises_value_error(self):
        result = WorkflowResult.unsupported('등록되지 않은 key')
        with self.assertRaises(ValueError) as ctx:
            build_reply_from_agent_result(result)
        self.assertIn('unsupported', str(ctx.exception))

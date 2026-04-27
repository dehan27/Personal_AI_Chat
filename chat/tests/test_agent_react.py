"""Phase 7-1 ReAct runtime 단위 테스트.

실제 LLM 호출 없이 `run_chat_completion` 만 mock 한다. 도구 callable 도 등록된
실제 도구 대신 mock 으로 갈아끼워, ReAct loop 자체의 분기·종료 정책을 고립
검증.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from chat.services.agent import react, tools as agent_tools
from chat.services.agent.react import (
    DEFAULT_MAX_ITERATIONS,
    MAX_CONSECUTIVE_FAILURES,
    MAX_REPEATED_CALL,
)
from chat.services.agent.tools import Tool
from chat.workflows.core import WorkflowStatus
from chat.workflows.domains.field_spec import FieldSpec


class _UsageStub:
    prompt_tokens = 30
    completion_tokens = 10
    total_tokens = 40


def _completion(*replies):
    """`run_chat_completion` 을 차례대로 반환하는 사이드이펙트 만들기."""
    iterator = iter(replies)

    def _side_effect(messages):
        return (next(iterator), _UsageStub(), 'gpt-4o-mini')

    return _side_effect


def _make_dummy_tool(name='dummy', *, callable_=None, summarize=None):
    return Tool(
        name=name,
        description='test only',
        input_schema={'query': FieldSpec(type='text', required=True)},
        callable=callable_ or (lambda args: f'echo:{args["query"]}'),
        summarize=summarize or (lambda r: f'ok: {r}'),
    )


class RunAgentTests(SimpleTestCase):
    """LLM·도구를 mock 으로 고정한 ReAct loop 단위 검증."""

    def setUp(self):
        # 기존 자동 등록된 세 도구는 비워두고 더미만 등록 — 격리.
        self._snapshot = agent_tools._snapshot_for_tests()
        agent_tools._reset_for_tests()
        agent_tools.register(_make_dummy_tool())

    def tearDown(self):
        agent_tools._restore_for_tests(self._snapshot)

    def _patch(self, llm_replies):
        return patch(
            'chat.services.agent.react.run_chat_completion',
            side_effect=_completion(*llm_replies),
        )

    def _patch_prompt(self):
        return patch(
            'chat.services.agent.prompts.load_prompt',
            return_value='[STUB SYSTEM]',
        )

    def _patch_record(self):
        return patch(
            'chat.services.agent.react.record_token_usage',
        )

    def test_immediate_final_answer(self):
        with self._patch_prompt(), self._patch_record(), self._patch([
            '{"thought": "쉬운 질문", "action": "final_answer", "answer": "31일"}',
        ]):
            r = react.run_agent('며칠?', history=[])
        self.assertEqual(r.status, WorkflowStatus.OK)
        self.assertEqual(r.value, '31일')

    def test_one_tool_call_then_final_answer(self):
        with self._patch_prompt(), self._patch_record(), self._patch([
            '{"thought": "검색", "action": "dummy", "arguments": {"query": "x"}}',
            '{"thought": "충분", "action": "final_answer", "answer": "결과"}',
        ]):
            r = react.run_agent('테스트', history=[])
        self.assertEqual(r.status, WorkflowStatus.OK)
        self.assertEqual(r.value, '결과')

    def test_max_iterations_exceeded_returns_upstream_error(self):
        # max_iterations 만큼 도구만 부르고 final_answer 를 안 내면 종료.
        # 인자를 모두 다르게 줘서 MAX_REPEATED_CALL 가드가 먼저 걸리지 않게 한다.
        replies = [
            f'{{"thought": "또 검색", "action": "dummy", "arguments": {{"query": "q{i}"}}}}'
            for i in range(DEFAULT_MAX_ITERATIONS)
        ]
        with self._patch_prompt(), self._patch_record(), self._patch(replies):
            r = react.run_agent('Q', history=[], max_iterations=DEFAULT_MAX_ITERATIONS)
        self.assertEqual(r.status, WorkflowStatus.UPSTREAM_ERROR)
        self.assertIn('도구를 너무 많이', r.details['reason'])

    def test_repeated_same_call_terminates_with_not_found(self):
        same_call = '{"thought": "반복", "action": "dummy", "arguments": {"query": "x"}}'
        replies = [same_call] * MAX_REPEATED_CALL
        with self._patch_prompt(), self._patch_record(), self._patch(replies):
            r = react.run_agent('Q', history=[])
        self.assertEqual(r.status, WorkflowStatus.NOT_FOUND)
        self.assertIn('남지 않아', r.details['reason'])

    def test_consecutive_tool_failures_trigger_no_more_useful_tools(self):
        # 도구 callable 이 매번 raise → Observation 이 모두 is_failure.
        agent_tools._reset_for_tests()
        agent_tools.register(_make_dummy_tool(
            callable_=lambda args: (_ for _ in ()).throw(RuntimeError('boom')),
        ))
        replies = [
            '{"thought": "1", "action": "dummy", "arguments": {"query": "a"}}',
            '{"thought": "2", "action": "dummy", "arguments": {"query": "b"}}',
            '{"thought": "3", "action": "dummy", "arguments": {"query": "c"}}',
        ]
        self.assertEqual(len(replies), MAX_CONSECUTIVE_FAILURES)
        with self._patch_prompt(), self._patch_record(), self._patch(replies):
            r = react.run_agent('Q', history=[])
        self.assertEqual(r.status, WorkflowStatus.NOT_FOUND)

    def test_unknown_action_keeps_loop_going_then_final(self):
        # action 이 등록되지 않은 이름이면 실패 Observation 으로 누적되지만 loop 진행.
        replies = [
            '{"thought": "?", "action": "ghost_tool", "arguments": {}}',
            '{"thought": "정리", "action": "final_answer", "answer": "OK"}',
        ]
        with self._patch_prompt(), self._patch_record(), self._patch(replies):
            r = react.run_agent('Q', history=[])
        self.assertEqual(r.status, WorkflowStatus.OK)
        self.assertEqual(r.value, 'OK')

    def test_invalid_json_retries_once_then_final(self):
        replies = [
            'not json',
            '{"thought": "복구", "action": "final_answer", "answer": "복구된 답"}',
        ]
        with self._patch_prompt(), self._patch_record(), self._patch(replies):
            r = react.run_agent('Q', history=[])
        self.assertEqual(r.status, WorkflowStatus.OK)
        self.assertEqual(r.value, '복구된 답')

    def test_invalid_json_twice_results_in_fatal_upstream_error(self):
        replies = ['not json', 'still bad']
        with self._patch_prompt(), self._patch_record(), self._patch(replies):
            r = react.run_agent('Q', history=[])
        self.assertEqual(r.status, WorkflowStatus.UPSTREAM_ERROR)

    def test_llm_exception_becomes_upstream_error(self):
        from chat.services.single_shot.types import QueryPipelineError
        with self._patch_prompt(), self._patch_record(), patch(
            'chat.services.agent.react.run_chat_completion',
            side_effect=QueryPipelineError('boom'),
        ):
            r = react.run_agent('Q', history=[])
        self.assertEqual(r.status, WorkflowStatus.UPSTREAM_ERROR)

    def test_empty_question_short_circuits_with_not_found(self):
        # LLM 호출 없이 즉시 INSUFFICIENT_EVIDENCE → NOT_FOUND.
        with self._patch_prompt(), self._patch_record(), patch(
            'chat.services.agent.react.run_chat_completion',
        ) as llm:
            r = react.run_agent('   ', history=[])
        llm.assert_not_called()
        self.assertEqual(r.status, WorkflowStatus.NOT_FOUND)
        self.assertIn('비어', r.details['reason'])

    def test_final_answer_with_empty_string_returns_not_found(self):
        replies = ['{"thought": "...", "action": "final_answer", "answer": ""}']
        with self._patch_prompt(), self._patch_record(), self._patch(replies):
            r = react.run_agent('Q', history=[])
        self.assertEqual(r.status, WorkflowStatus.NOT_FOUND)

    def test_arguments_must_be_object(self):
        # arguments 가 문자열로 오면 호출하지 않고 실패 Observation → 다음 iteration.
        replies = [
            '{"thought": "잘못", "action": "dummy", "arguments": "x"}',
            '{"thought": "정리", "action": "final_answer", "answer": "끝"}',
        ]
        with self._patch_prompt(), self._patch_record(), self._patch(replies):
            r = react.run_agent('Q', history=[])
        self.assertEqual(r.status, WorkflowStatus.OK)
        self.assertEqual(r.value, '끝')

    def test_token_usage_recorded_per_llm_call(self):
        replies = [
            '{"thought": "1", "action": "dummy", "arguments": {"query": "a"}}',
            '{"thought": "2", "action": "final_answer", "answer": "z"}',
        ]
        with self._patch_prompt(), self._patch([
            *replies,
        ]), patch(
            'chat.services.agent.react.record_token_usage',
        ) as record:
            react.run_agent('Q', history=[])
        # LLM 이 두 번 돌았으므로 record_token_usage 도 두 번.
        self.assertEqual(record.call_count, 2)

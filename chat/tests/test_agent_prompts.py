"""Phase 7-1 agent.prompts 단위 테스트 — 메시지 빌더가 도구 카탈로그·관찰을 올바르게 직렬화하는지."""

from unittest.mock import patch

from django.test import SimpleTestCase

from chat.services.agent import prompts as prompt_module
from chat.services.agent.state import AgentState
from chat.services.agent.tools import all_entries


def _build(state):
    """load_prompt 는 외부 파일 의존이라 system 부분만 stub 하고 user payload 만 본다."""
    with patch(
        'chat.services.agent.prompts.load_prompt',
        return_value='[STUB SYSTEM]',
    ):
        return prompt_module.build_messages(state)


class BuildMessagesTests(SimpleTestCase):
    def test_two_messages_returned(self):
        state = AgentState(question='이 두 문서 비교해줘', history=[])
        messages = _build(state)
        self.assertEqual([m['role'] for m in messages], ['system', 'user'])

    def test_user_payload_includes_question(self):
        state = AgentState(question='경조사 규정 알려줘', history=[])
        messages = _build(state)
        self.assertIn('Question: 경조사 규정 알려줘', messages[1]['content'])

    def test_user_payload_lists_registered_tools(self):
        state = AgentState(question='Q', history=[])
        payload = _build(state)[1]['content']
        # 부팅 시점 등록된 세 도구 모두 카탈로그에 있어야 함.
        for name in ('retrieve_documents', 'find_canonical_qa', 'run_workflow'):
            self.assertIn(name, payload)

    def test_run_workflow_described_as_free_form(self):
        state = AgentState(question='Q', history=[])
        payload = _build(state)[1]['content']
        # raw 모드 도구는 schema 가 아니라 "free-form" 으로 표기.
        self.assertIn('run_workflow', payload)
        self.assertIn('free-form', payload)

    def test_user_payload_includes_recent_observations(self):
        state = AgentState(question='Q', history=[])
        state.add_observation('retrieve_documents', '3건 발견', is_failure=False)
        state.add_observation('find_canonical_qa', '0건', is_failure=True)
        payload = _build(state)[1]['content']
        self.assertIn('3건 발견', payload)
        self.assertIn('[FAIL]', payload)
        self.assertIn('0건', payload)

    def test_user_payload_caps_observations_count(self):
        state = AgentState(question='Q', history=[])
        for i in range(prompt_module.MAX_RECENT_OBSERVATIONS + 5):
            state.add_observation('t', f'obs {i}')
        payload = _build(state)[1]['content']
        # 가장 오래된 항목은 잘려야 한다.
        self.assertNotIn('obs 0', payload)
        self.assertIn(f'obs {prompt_module.MAX_RECENT_OBSERVATIONS + 4}', payload)

    def test_user_payload_includes_iteration_count(self):
        state = AgentState(question='Q', history=[], iteration_count=3)
        payload = _build(state)[1]['content']
        self.assertIn('iteration=3', payload)

    def test_user_payload_ends_with_return_directive(self):
        state = AgentState(question='Q', history=[])
        payload = _build(state)[1]['content']
        self.assertTrue(payload.rstrip().endswith('Return JSON only:'))

    def test_last_tool_call_surfaced_for_repeat_avoidance(self):
        state = AgentState(question='Q', history=[])
        state.record_tool_call('retrieve_documents', {'query': '경조사'})
        payload = _build(state)[1]['content']
        self.assertIn('Last tool call: retrieve_documents', payload)
        self.assertIn('경조사', payload)

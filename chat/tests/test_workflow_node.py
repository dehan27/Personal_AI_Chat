"""Phase 6-1 graph workflow_node 단위 테스트.

- single_shot 폴백 경로 (key 비었거나 미등록) 는 run_single_shot 을 mock 해 실제
  호출 여부만 확인.
- registered key 경로는 dispatch 의 반환 `WorkflowResult` 가 reply 문자열로
  QueryResult.reply 에 실리는지 확인 (date_calculation 실제 구현을 그대로 씀).
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from chat.graph.nodes.workflow import workflow_node
from chat.services.single_shot.types import QueryResult


class WorkflowNodeTests(SimpleTestCase):
    def test_empty_workflow_key_falls_back_to_single_shot(self):
        state = {'question': 'Q', 'history': [], 'workflow_key': ''}
        with patch(
            'chat.graph.nodes.workflow.single_shot_node',
            return_value={'result': QueryResult('OK', [], 0, None)},
        ) as mocked:
            out = workflow_node(state)
        mocked.assert_called_once_with(state)
        self.assertEqual(out['result'].reply, 'OK')

    def test_unknown_workflow_key_falls_back_to_single_shot(self):
        state = {'question': 'Q', 'history': [], 'workflow_key': 'ghost'}
        with patch(
            'chat.graph.nodes.workflow.single_shot_node',
            return_value={'result': QueryResult('OK', [], 0, None)},
        ) as mocked:
            out = workflow_node(state)
        mocked.assert_called_once_with(state)
        self.assertEqual(out['result'].reply, 'OK')

    def test_registered_key_runs_dispatch_and_formats_reply(self):
        # registry 에 부팅 시점 등록된 date_calculation 을 그대로 활용.
        # workflow_input 으로 start/end 를 주면 OK 가 떨어지고 reply 에 포맷된 문자열이 담긴다.
        state = {
            'question': 'Q',
            'history': [],
            'workflow_key': 'date_calculation',
            'workflow_input': {'start': '2025-01-01', 'end': '2025-01-31'},
        }
        out = workflow_node(state)
        result = out['result']
        self.assertIsInstance(result, QueryResult)
        self.assertIn('2025-01-01', result.reply)
        self.assertIn('2025-01-31', result.reply)
        self.assertIn('30일', result.reply)
        self.assertEqual(result.sources, [])
        self.assertEqual(result.total_tokens, 0)
        self.assertIsNone(result.chat_log_id)

    def test_registered_key_with_missing_input_returns_guide_reply(self):
        state = {
            'question': 'Q',
            'history': [],
            'workflow_key': 'date_calculation',
            # workflow_input 생략 → MISSING_INPUT.
        }
        out = workflow_node(state)
        self.assertIn('start', out['result'].reply)
        self.assertIn('end', out['result'].reply)
        self.assertIn('필요', out['result'].reply)

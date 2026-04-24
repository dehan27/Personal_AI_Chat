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

    def test_registered_key_with_empty_explicit_input_returns_guide_reply(self):
        # workflow_input 을 빈 dict 로 명시 전달 → extractor 스킵, MISSING_INPUT.
        state = {
            'question': 'Q',
            'history': [],
            'workflow_key': 'date_calculation',
            'workflow_input': {},
        }
        out = workflow_node(state)
        self.assertIn('start', out['result'].reply)
        self.assertIn('end', out['result'].reply)
        self.assertIn('필요', out['result'].reply)

    def test_natural_language_question_goes_through_extractor(self):
        # workflow_input 미지정 → extractor 가 질문에서 start/end/unit 을 뽑는다.
        # LLM fallback 은 regex 가 전부 채울 수 있으면 트리거되지 않는다.
        state = {
            'question': '2025-01-01 부터 2025-02-01 까지 며칠이야?',
            'history': [],
            'workflow_key': 'date_calculation',
        }
        out = workflow_node(state)
        self.assertIn('2025-01-01', out['result'].reply)
        self.assertIn('2025-02-01', out['result'].reply)
        self.assertIn('31일', out['result'].reply)

    def test_amount_calculation_end_to_end_from_natural_language(self):
        # amount_calculation + 자연어 평균 질문이 실제로 답까지 이어지는지 확인.
        state = {
            'question': '1,000원, 2,000원, 3,000원 평균이 얼마야?',
            'history': [],
            'workflow_key': 'amount_calculation',
        }
        out = workflow_node(state)
        self.assertIn('평균', out['result'].reply)
        self.assertIn('2,000.00', out['result'].reply)

    def test_amount_calculation_sum_default_op(self):
        state = {
            'question': '100 200 300 합계는?',
            'history': [],
            'workflow_key': 'amount_calculation',
        }
        out = workflow_node(state)
        self.assertIn('합계', out['result'].reply)
        self.assertIn('600', out['result'].reply)

    def test_extractor_token_usage_recorded_when_llm_invoked(self):
        # LLM fallback 이 실제로 돈 상황을 mock 으로 재현하고, record_token_usage
        # 가 한 번 호출되는지 확인.
        from unittest.mock import patch

        class _Usage:
            prompt_tokens = 20
            completion_tokens = 5
            total_tokens = 25

        state = {
            'question': '2025-01-01 이후 며칠?',
            'history': [],
            'workflow_key': 'date_calculation',
        }
        with patch(
            'chat.graph.nodes.workflow.extract_workflow_input',
            return_value=(
                {'start': '2025-01-01', 'end': '2025-02-01', 'unit': 'days'},
                _Usage(), 'gpt-4o-mini',
            ),
        ), patch(
            'chat.graph.nodes.workflow.record_token_usage',
        ) as mocked_record:
            out = workflow_node(state)

        mocked_record.assert_called_once()
        args, _ = mocked_record.call_args
        self.assertEqual(args[0], 'gpt-4o-mini')
        self.assertIn('31일', out['result'].reply)

"""Phase 7-2 graph agent_node 단위 테스트.

`run_agent` / `rewrite_query_with_history` / `record_token_usage` 모두 mock 해서
agent_node 자체의 분기·합성 동작만 격리 검증. graph 결선 (`add_node` /
conditional edge) 자체는 `test_graph_agent_wiring.py` 의 책임.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from chat.graph.nodes.agent import agent_node
from chat.services.single_shot.types import QueryResult
from chat.workflows.core import WorkflowResult


def _ok(value):
    return WorkflowResult.ok(
        value=value,
        details={'termination': 'final_answer'},
    )


def _not_found(reason=''):
    return WorkflowResult.not_found(reason)


def _upstream_error(reason=''):
    return WorkflowResult.upstream_error(reason)


class _UsageStub:
    prompt_tokens = 10
    completion_tokens = 5
    total_tokens = 15


class AgentNodeTests(SimpleTestCase):
    def _patch_runtime(self, agent_result):
        return patch(
            'chat.graph.nodes.agent.run_agent',
            return_value=agent_result,
        )

    def _patch_rewriter(self, *, return_value=None, side_effect=None):
        kwargs = {}
        if side_effect is not None:
            kwargs['side_effect'] = side_effect
        else:
            kwargs['return_value'] = return_value or ('rewritten Q', None, None)
        return patch(
            'chat.graph.nodes.agent.rewrite_query_with_history',
            **kwargs,
        )

    def _patch_token_recorder(self):
        return patch('chat.graph.nodes.agent.record_token_usage')

    # ---------- history 분기 ----------

    def test_empty_history_skips_rewriter(self):
        with self._patch_runtime(_ok('답')) as run, \
                self._patch_rewriter() as rw, \
                self._patch_token_recorder() as record:
            out = agent_node({'question': 'Q', 'history': []})

        rw.assert_not_called()
        run.assert_called_once_with('Q', history=[])
        record.assert_not_called()
        self.assertIsInstance(out['result'], QueryResult)
        self.assertEqual(out['result'].reply, '답')

    def test_history_present_calls_rewriter_and_uses_rewritten_question(self):
        with self._patch_runtime(_ok('답')) as run, \
                self._patch_rewriter(return_value=('자립 검색어', _UsageStub(), 'gpt-4o-mini')) as rw, \
                self._patch_token_recorder() as record:
            out = agent_node({
                'question': 'Q',
                'history': [{'role': 'user', 'content': '이전 질문'}],
            })

        rw.assert_called_once()
        # run_agent 가 rewritten 결과로 호출됐는지.
        run.assert_called_once_with('자립 검색어', history=[{'role': 'user', 'content': '이전 질문'}])
        record.assert_called_once_with('gpt-4o-mini', rw.return_value[1])
        self.assertEqual(out['result'].reply, '답')

    def test_rewriter_usage_none_skips_record(self):
        with self._patch_runtime(_ok('답')), \
                self._patch_rewriter(return_value=('Q', None, None)) as rw, \
                self._patch_token_recorder() as record:
            out = agent_node({
                'question': 'Q',
                'history': [{'role': 'user', 'content': 'x'}],
            })

        rw.assert_called_once()
        record.assert_not_called()
        self.assertEqual(out['result'].reply, '답')

    def test_token_record_failure_does_not_break_reply(self):
        with self._patch_runtime(_ok('답')), \
                self._patch_rewriter(return_value=('Q', _UsageStub(), 'gpt-4o-mini')), \
                patch(
                    'chat.graph.nodes.agent.record_token_usage',
                    side_effect=RuntimeError('db down'),
                ):
            out = agent_node({
                'question': 'Q',
                'history': [{'role': 'user', 'content': 'x'}],
            })
        self.assertEqual(out['result'].reply, '답')

    # ---------- status 별 reply ----------

    def test_ok_result_passes_value_to_reply(self):
        with self._patch_runtime(_ok('자료에 따르면 ...')), \
                self._patch_rewriter(), \
                self._patch_token_recorder():
            out = agent_node({'question': 'Q', 'history': []})
        self.assertEqual(out['result'].reply, '자료에 따르면 ...')

    def test_not_found_result_uses_reason(self):
        with self._patch_runtime(_not_found('근거가 부족합니다.')), \
                self._patch_rewriter(), \
                self._patch_token_recorder():
            out = agent_node({'question': 'Q', 'history': []})
        self.assertEqual(out['result'].reply, '근거가 부족합니다.')

    def test_upstream_error_result_uses_reason(self):
        with self._patch_runtime(_upstream_error('잠시 후 다시 시도해 주세요.')), \
                self._patch_rewriter(), \
                self._patch_token_recorder():
            out = agent_node({'question': 'Q', 'history': []})
        self.assertEqual(out['result'].reply, '잠시 후 다시 시도해 주세요.')

    # ---------- 반환 형태 ----------

    def test_return_shape_matches_query_result_contract(self):
        with self._patch_runtime(_ok('답')), \
                self._patch_rewriter(), \
                self._patch_token_recorder():
            out = agent_node({'question': 'Q', 'history': []})

        self.assertIn('result', out)
        result = out['result']
        self.assertIsInstance(result, QueryResult)
        self.assertEqual(result.sources, [])
        self.assertEqual(result.total_tokens, 0)
        self.assertIsNone(result.chat_log_id)

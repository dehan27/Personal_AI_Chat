"""query_rewriter 단위 테스트 (Phase 4-3).

회귀 0 을 보장하는 세 가지 fallback 경로와 일반 재작성 경로를 검증한다.
LLM 실제 호출은 하지 않고 `run_chat_completion` 만 mock 한다.
"""

from unittest.mock import patch

from django.test import TestCase

from chat.services import query_rewriter


def _stub_completion(text: str):
    """`run_chat_completion` 의 세 튜플 반환(reply, usage, model) 을 흉내."""
    class _Usage:
        prompt_tokens = 10
        completion_tokens = 4
        total_tokens = 14
    return text, _Usage(), 'gpt-4o-mini'


class QueryRewriterTests(TestCase):
    def test_empty_history_returns_original_without_llm_call(self):
        with patch('chat.services.query_rewriter.run_chat_completion') as mocked:
            result, usage, model = query_rewriter.rewrite_query_with_history(
                '비싼거',
                history=[],
            )
        self.assertEqual(result, '비싼거')
        self.assertIsNone(usage)
        self.assertIsNone(model)
        mocked.assert_not_called()

    def test_noop_sentinel_keeps_original_question(self):
        history = [
            {'role': 'user', 'content': '퇴직금 계산식 알려줘'},
            {'role': 'assistant', 'content': '퇴직금 = 평균임금 × 30일 × 근속연수/365 ...'},
        ]
        with patch(
            'chat.services.query_rewriter.run_chat_completion',
            return_value=_stub_completion(query_rewriter.NOOP_SENTINEL),
        ):
            result, usage, model = query_rewriter.rewrite_query_with_history(
                '퇴직금 계산식 알려줘',
                history=history,
            )
        # NOOP 이라도 LLM 이 돌긴 했으므로 usage 는 기록 대상이다.
        self.assertEqual(result, '퇴직금 계산식 알려줘')
        self.assertIsNotNone(usage)
        self.assertEqual(model, 'gpt-4o-mini')

    def test_llm_failure_falls_back_to_original(self):
        history = [{'role': 'user', 'content': '이전 질문'}]
        from chat.services.single_shot.types import QueryPipelineError
        with patch(
            'chat.services.query_rewriter.run_chat_completion',
            side_effect=QueryPipelineError('network down'),
        ):
            result, usage, model = query_rewriter.rewrite_query_with_history(
                '비싼거',
                history=history,
            )
        self.assertEqual(result, '비싼거')
        self.assertIsNone(usage)
        self.assertIsNone(model)

    def test_follow_up_uses_rewritten_query(self):
        history = [
            {'role': 'user', 'content': '경조사 규정 알려줘'},
            {'role': 'assistant', 'content': '경조사 규정: 본인 상 500만원, 배우자 상 100만원 ...'},
        ]
        with patch(
            'chat.services.query_rewriter.run_chat_completion',
            return_value=_stub_completion('경조사 중 가장 비싼 항목'),
        ):
            result, usage, model = query_rewriter.rewrite_query_with_history(
                '비싼거',
                history=history,
            )
        self.assertEqual(result, '경조사 중 가장 비싼 항목')
        self.assertEqual(model, 'gpt-4o-mini')
        self.assertIsNotNone(usage)

    def test_llm_output_cleanup_strips_quotes_and_prefix(self):
        history = [{'role': 'user', 'content': '이전 질문'}]
        # 모델이 가끔 뱉는 흔한 프롬프트 탈선 패턴 — 접두어 + 외곽 따옴표.
        with patch(
            'chat.services.query_rewriter.run_chat_completion',
            return_value=_stub_completion('검색어: "연차 일수"'),
        ):
            result, _, _ = query_rewriter.rewrite_query_with_history(
                '몇 일?',
                history=history,
            )
        self.assertEqual(result, '연차 일수')

"""openai_usage 단위 테스트 (Phase 4-4).

OpenAI Admin API 를 실제로 호출하지 않는다 — `_get_json` 을 mock 해
응답을 주입하고 집계 로직만 검증한다.
"""

from datetime import datetime, timezone
from unittest.mock import patch

from django.test import TestCase

from chat.services import openai_usage


_BUCKET_TS = int(datetime(2026, 4, 22, tzinfo=timezone.utc).timestamp())


def _bucket(input_tokens=0, output_tokens=0, model=None, cost_value=None):
    """테스트에서 쓰는 가짜 버킷 한 덩어리."""
    if cost_value is not None:
        result = {'amount': {'value': cost_value, 'currency': 'usd'}}
    else:
        result = {'input_tokens': input_tokens, 'output_tokens': output_tokens}
        if model:
            result['model'] = model
    return {
        'object': 'bucket',
        'start_time': _BUCKET_TS,
        'end_time': _BUCKET_TS + 86400,
        'results': [result],
    }


def _make_get_json(responses_by_path):
    """호출된 path 를 보고 미리 준비한 응답을 돌려주는 가짜."""
    def side_effect(admin_key, path, params):  # noqa: ARG001
        return responses_by_path.get(path, {'data': [], 'has_more': False})
    return side_effect


class OpenAIUsageTests(TestCase):
    def test_missing_admin_key_raises_controlled_error(self):
        with patch.dict('os.environ', {'OPENAI_ADMIN_KEY': ''}, clear=False):
            with self.assertRaises(openai_usage.AdminKeyMissing):
                openai_usage.fetch_usage_summary()

    def test_aggregates_totals_across_endpoints(self):
        responses = {
            'usage/completions': {
                'data': [_bucket(input_tokens=1000, output_tokens=200)],
                'has_more': False,
            },
            'usage/embeddings': {
                'data': [_bucket(input_tokens=300)],
                'has_more': False,
            },
            'costs': {
                'data': [_bucket(cost_value=0.42)],
                'has_more': False,
            },
        }
        with patch.dict('os.environ', {'OPENAI_ADMIN_KEY': 'sk-admin-test'}):
            with patch(
                'chat.services.openai_usage._get_json',
                side_effect=_make_get_json(responses),
            ):
                summary = openai_usage.fetch_usage_summary(
                    now=datetime(2026, 4, 24, tzinfo=timezone.utc),
                )

        # 최근 7일은 같은 응답을 재사용 — completions 쪽이 한번, embeddings 쪽이 한번,
        # group_by=model 호출이 한번 더 나가지만 mock 은 path 만 보고 돌려주므로 합산 가능.
        total = summary['total']
        self.assertEqual(total['input_tokens'], 1300)   # completions 1000 + embeddings 300
        self.assertEqual(total['output_tokens'], 200)
        self.assertEqual(total['total_tokens'], 1500)
        self.assertAlmostEqual(total['cost_usd'], 0.42)

    def test_handles_empty_result_list(self):
        responses = {
            'usage/completions': {'data': [{'results': []}], 'has_more': False},
            'usage/embeddings': {'data': [{'results': []}], 'has_more': False},
            'costs': {'data': [{'results': []}], 'has_more': False},
        }
        with patch.dict('os.environ', {'OPENAI_ADMIN_KEY': 'sk-admin-test'}):
            with patch(
                'chat.services.openai_usage._get_json',
                side_effect=_make_get_json(responses),
            ):
                summary = openai_usage.fetch_usage_summary(
                    now=datetime(2026, 4, 24, tzinfo=timezone.utc),
                )

        self.assertEqual(summary['total']['total_tokens'], 0)
        self.assertEqual(summary['last_7d']['daily'], [])
        self.assertEqual(summary['last_7d']['by_model'], [])

    def test_by_model_ordering_and_aggregation(self):
        # completions 에 두 모델이 섞여서 나오는 케이스
        completion_buckets = [
            {
                'object': 'bucket',
                'start_time': _BUCKET_TS,
                'end_time': _BUCKET_TS + 86400,
                'results': [
                    {'model': 'gpt-4o-mini', 'input_tokens': 500, 'output_tokens': 100},
                    {'model': 'gpt-4o',      'input_tokens': 50,  'output_tokens': 20},
                ],
            },
        ]
        responses = {
            'usage/completions': {'data': completion_buckets, 'has_more': False},
            'usage/embeddings': {'data': [], 'has_more': False},
            'costs': {'data': [], 'has_more': False},
        }
        with patch.dict('os.environ', {'OPENAI_ADMIN_KEY': 'sk-admin-test'}):
            with patch(
                'chat.services.openai_usage._get_json',
                side_effect=_make_get_json(responses),
            ):
                summary = openai_usage.fetch_usage_summary(
                    now=datetime(2026, 4, 24, tzinfo=timezone.utc),
                )

        by_model = summary['last_7d']['by_model']
        self.assertEqual([row['model'] for row in by_model], ['gpt-4o-mini', 'gpt-4o'])
        self.assertEqual(by_model[0]['tokens'], 600)  # 500 + 100
        self.assertEqual(by_model[1]['tokens'], 70)   # 50 + 20

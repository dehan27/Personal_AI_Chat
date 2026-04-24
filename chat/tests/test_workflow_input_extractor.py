"""Phase 6-2 workflow_input_extractor 단위 테스트."""

from unittest.mock import patch

from django.test import SimpleTestCase

from chat.services.workflow_input_extractor import extract
from chat.workflows.domains.field_spec import FieldSpec


def _mock_llm_off():
    """LLM fallback 를 실제로 호출하지 않도록 막는 patch helper.

    regex 경로만 검증하는 테스트는 이 patch 하에서 돌린다.
    """
    return patch(
        'chat.services.workflow_input_extractor._call_llm_extractor',
        return_value=(None, None, None),
    )


_DATE_SCHEMA = {
    'start': FieldSpec(type='date', required=True, aliases=('시작', '부터')),
    'end': FieldSpec(type='date', required=True, aliases=('종료', '까지')),
    'unit': FieldSpec(
        type='enum', required=False, default='days',
        enum_values={
            'days': ('일', '며칠'),
            'months': ('개월', '달'),
            'years': ('년', 'years'),
        },
    ),
}

_AMOUNT_SCHEMA = {
    'values': FieldSpec(type='number_list', required=True),
    'op': FieldSpec(
        type='enum', required=False, default='sum',
        enum_values={
            'sum': ('합계', '합'),
            'average': ('평균',),
            'diff': ('차이',),
        },
    ),
}


class ExtractBasicShapeTests(SimpleTestCase):
    def test_empty_schema_returns_empty_dict(self):
        # LLM 경로 자체가 스킵된다는 의미로, 모든 required 가 없으므로 LLM 미호출.
        out, usage, model = extract('1 + 2', [], {})
        self.assertEqual(out, {})
        self.assertIsNone(usage)
        self.assertIsNone(model)


class ExtractDateSchemaTests(SimpleTestCase):
    def setUp(self):
        self._llm_patch = _mock_llm_off()
        self._llm_patch.start()
        self.addCleanup(self._llm_patch.stop)

    def test_two_iso_dates_assigned_in_declaration_order(self):
        out, *_ = extract(
            '2025-01-01 부터 2025-02-01 까지 며칠이야?',
            [], _DATE_SCHEMA,
        )
        self.assertEqual(out.get('start'), '2025-01-01')
        self.assertEqual(out.get('end'), '2025-02-01')

    def test_korean_natural_dates(self):
        out, *_ = extract(
            '2025년 1월 1일부터 2025년 3월 15일까지 몇개월?',
            [], _DATE_SCHEMA,
        )
        self.assertIn('년 1월', out.get('start'))
        self.assertIn('3월 15일', out.get('end'))

    def test_unit_enum_matched_by_token(self):
        out, *_ = extract(
            '2024-01-01 부터 2024-02-01 까지 몇 달이야?',
            [], _DATE_SCHEMA,
        )
        self.assertEqual(out.get('unit'), 'months')

    def test_default_applied_when_no_enum_token(self):
        out, *_ = extract(
            '2024-01-01 2024-02-01 차이',
            [], _DATE_SCHEMA,
        )
        self.assertEqual(out.get('unit'), 'days')

    def test_missing_one_date_leaves_field_absent_when_llm_unavailable(self):
        out, *_ = extract('2024-01-01 이후 며칠?', [], _DATE_SCHEMA)
        self.assertEqual(out.get('start'), '2024-01-01')
        # LLM fallback 이 막혀있으므로 end 는 비어야 한다.
        self.assertNotIn('end', out)


class ExtractAmountSchemaTests(SimpleTestCase):
    def setUp(self):
        self._llm_patch = _mock_llm_off()
        self._llm_patch.start()
        self.addCleanup(self._llm_patch.stop)

    def test_money_values_collected_into_list(self):
        out, *_ = extract(
            '1,000원과 2,000원과 3,000원 합계는?',
            [], _AMOUNT_SCHEMA,
        )
        self.assertEqual(out.get('values'), [1000, 2000, 3000])
        self.assertEqual(out.get('op'), 'sum')

    def test_plain_numbers_without_won(self):
        out, *_ = extract('100 200 300 평균', [], _AMOUNT_SCHEMA)
        self.assertEqual(out.get('values'), [100, 200, 300])
        self.assertEqual(out.get('op'), 'average')

    def test_diff_op_token(self):
        out, *_ = extract('100 200 차이', [], _AMOUNT_SCHEMA)
        self.assertEqual(out.get('op'), 'diff')

    def test_default_op_when_unspecified(self):
        out, *_ = extract('100 200', [], _AMOUNT_SCHEMA)
        self.assertEqual(out.get('op'), 'sum')


class ExtractMoneyMaskingTests(SimpleTestCase):
    def setUp(self):
        self._llm_patch = _mock_llm_off()
        self._llm_patch.start()
        self.addCleanup(self._llm_patch.stop)

    def test_money_field_takes_priority_over_number_field(self):
        schema = {
            'price': FieldSpec(type='money'),
            'count': FieldSpec(type='number'),
        }
        out, *_ = extract('1000원에 3개 사줘', [], schema)
        self.assertEqual(out.get('price'), 1000)
        self.assertEqual(out.get('count'), 3)

    def test_number_list_ignores_money_spans(self):
        schema = {
            'price': FieldSpec(type='money'),
            'values': FieldSpec(type='number_list'),
        }
        out, *_ = extract('1,000원 외에도 50 60 70', [], schema)
        self.assertEqual(out.get('price'), 1000)
        self.assertEqual(out.get('values'), [50, 60, 70])


class ExtractTextSchemaTests(SimpleTestCase):
    """Phase 6-3: 'text' 타입 필드는 질문 원문을 그대로 담는다."""

    def setUp(self):
        self._llm_patch = _mock_llm_off()
        self._llm_patch.start()
        self.addCleanup(self._llm_patch.stop)

    def test_required_text_field_filled_with_full_question(self):
        schema = {'query': FieldSpec(type='text', required=True)}
        out, usage, model = extract(
            '표에서 본인 상 경조금 알려줘',
            [],
            schema,
        )
        self.assertEqual(out['query'], '표에서 본인 상 경조금 알려줘')
        # text 필드는 질문으로 채워지니 LLM 호출이 일어날 이유 없음.
        self.assertIsNone(usage)
        self.assertIsNone(model)

    def test_text_field_trims_whitespace(self):
        schema = {'query': FieldSpec(type='text', required=True)}
        out, *_ = extract('   몇 개월이야?  ', [], schema)
        self.assertEqual(out['query'], '몇 개월이야?')

    def test_text_field_uses_default_when_question_empty(self):
        schema = {
            'query': FieldSpec(
                type='text', required=False, default='(empty)',
            ),
        }
        out, *_ = extract('', [], schema)
        self.assertEqual(out['query'], '(empty)')

    def test_mixed_schema_text_plus_other_fields(self):
        schema = {
            'start': FieldSpec(type='date', required=True),
            'query': FieldSpec(type='text', required=True),
        }
        out, *_ = extract(
            '2025-01-01 부터 기록을 찾아줘',
            [],
            schema,
        )
        # 날짜는 regex 로, query 는 전체 질문으로.
        self.assertEqual(out['start'], '2025-01-01')
        self.assertEqual(out['query'], '2025-01-01 부터 기록을 찾아줘')


class _UsageStub:
    prompt_tokens = 10
    completion_tokens = 5
    total_tokens = 15


class LLMFallbackTests(SimpleTestCase):
    """LLM 이 regex 가 놓친 필드를 채워주는 시나리오."""

    def test_llm_fills_missing_required_field(self):
        # start 만 찍혀 있고 end 는 LLM 이 보강한다.
        with patch(
            'chat.services.workflow_input_extractor.run_chat_completion',
            return_value=('{"end": "2024-03-01"}', _UsageStub(), 'gpt-4o-mini'),
        ), patch(
            'chat.services.workflow_input_extractor.load_prompt',
            return_value='PROMPT',
        ):
            out, usage, model = extract(
                '2024-01-01 이후로 며칠?',
                [{'role': 'user', 'content': '종료일은 2024-03-01 로 해줘'}],
                _DATE_SCHEMA,
            )
        self.assertEqual(out['start'], '2024-01-01')
        self.assertEqual(out['end'], '2024-03-01')
        self.assertIs(usage, None if usage is None else usage)  # 체크가 목적이 아니라 값 있음만.
        self.assertEqual(model, 'gpt-4o-mini')

    def test_llm_failure_keeps_regex_only_result(self):
        with patch(
            'chat.services.workflow_input_extractor.run_chat_completion',
            side_effect=__import__(
                'chat.services.single_shot.types', fromlist=['QueryPipelineError'],
            ).QueryPipelineError('boom'),
        ), patch(
            'chat.services.workflow_input_extractor.load_prompt',
            return_value='PROMPT',
        ):
            out, usage, model = extract(
                '2024-01-01 이후로 며칠?',
                [],
                _DATE_SCHEMA,
            )
        self.assertEqual(out.get('start'), '2024-01-01')
        self.assertNotIn('end', out)
        self.assertIsNone(usage)
        self.assertIsNone(model)

    def test_llm_json_garbage_ignored(self):
        with patch(
            'chat.services.workflow_input_extractor.run_chat_completion',
            return_value=('not json at all', _UsageStub(), 'gpt-4o-mini'),
        ), patch(
            'chat.services.workflow_input_extractor.load_prompt',
            return_value='PROMPT',
        ):
            out, usage, model = extract(
                '2024-01-01 이후로 며칠?',
                [],
                _DATE_SCHEMA,
            )
        self.assertNotIn('end', out)
        # 호출이 실제 일어났고 usage 는 기록해도 무방 (실패지만 호출 비용은 소비됨).
        self.assertEqual(model, 'gpt-4o-mini')

    def test_llm_ignored_when_enum_outside_allowed_keys(self):
        with patch(
            'chat.services.workflow_input_extractor.run_chat_completion',
            return_value=('{"end": "2024-03-01", "unit": "weeks"}', _UsageStub(), 'gpt-4o-mini'),
        ), patch(
            'chat.services.workflow_input_extractor.load_prompt',
            return_value='PROMPT',
        ):
            out, *_ = extract('2024-01-01 부터 며칠?', [], _DATE_SCHEMA)
        # default='days' 가 먼저 채워졌으므로 LLM 의 'weeks' 는 무시되고 바뀌지 않는다.
        self.assertEqual(out.get('unit'), 'days')
        self.assertEqual(out.get('end'), '2024-03-01')

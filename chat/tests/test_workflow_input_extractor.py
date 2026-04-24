"""Phase 6-2 workflow_input_extractor 단위 테스트 — regex/토큰 경로."""

from django.test import SimpleTestCase

from chat.services.workflow_input_extractor import extract
from chat.workflows.domains.field_spec import FieldSpec


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
        out, usage, model = extract('1 + 2', [], {})
        self.assertEqual(out, {})
        self.assertIsNone(usage)
        self.assertIsNone(model)


class ExtractDateSchemaTests(SimpleTestCase):
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

    def test_missing_one_date_leaves_field_absent(self):
        out, *_ = extract('2024-01-01 이후 며칠?', [], _DATE_SCHEMA)
        self.assertEqual(out.get('start'), '2024-01-01')
        self.assertNotIn('end', out)


class ExtractAmountSchemaTests(SimpleTestCase):
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

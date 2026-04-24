"""Phase 6-1 reply formatter 단위 테스트."""

from django.test import SimpleTestCase

from chat.workflows.core import WorkflowResult, WorkflowStatus
from chat.workflows.domains.reply import build_reply_from_result


class BuildReplyTests(SimpleTestCase):
    def test_ok_date_calculation_uses_specialized_formatter(self):
        result = WorkflowResult.ok(
            31,
            details={
                'start': '2025-01-01',
                'end': '2025-02-01',
                'unit': 'days',
                'unit_label': '일',
            },
        )
        reply = build_reply_from_result(result, workflow_key='date_calculation')
        self.assertIn('2025-01-01', reply)
        self.assertIn('2025-02-01', reply)
        self.assertIn('31일', reply)

    def test_ok_amount_calculation_sum(self):
        result = WorkflowResult.ok(
            6000,
            details={'op': 'sum', 'values': [1000, 2000, 3000], 'count': 3},
        )
        reply = build_reply_from_result(result, workflow_key='amount_calculation')
        self.assertIn('합계', reply)
        self.assertIn('6,000', reply)

    def test_ok_amount_calculation_average_formats_float(self):
        result = WorkflowResult.ok(
            216.6666666,
            details={'op': 'average', 'values': [100, 200, 350], 'count': 3},
        )
        reply = build_reply_from_result(result, workflow_key='amount_calculation')
        self.assertIn('평균', reply)
        self.assertIn('216.67', reply)

    def test_ok_amount_calculation_diff(self):
        result = WorkflowResult.ok(
            40,
            details={'op': 'diff', 'values': [10, 50, 30], 'count': 3},
        )
        reply = build_reply_from_result(result, workflow_key='amount_calculation')
        self.assertIn('차이', reply)
        self.assertIn('40', reply)

    def test_ok_unknown_key_falls_back_to_default_formatter(self):
        result = WorkflowResult.ok(42)
        reply = build_reply_from_result(result, workflow_key='unknown')
        self.assertIn('42', reply)

    def test_missing_input_lists_fields(self):
        result = WorkflowResult.missing_input(['start', 'end'])
        reply = build_reply_from_result(result, workflow_key='date_calculation')
        self.assertIn('start', reply)
        self.assertIn('end', reply)
        self.assertIn('필요', reply)

    def test_missing_input_without_fields_falls_back(self):
        # 이론적으로는 팩토리가 허용하지 않지만, 직접 생성했을 때 안전한 기본 문구가 나오는지.
        result = WorkflowResult(status=WorkflowStatus.MISSING_INPUT)
        reply = build_reply_from_result(result, workflow_key='date_calculation')
        self.assertIn('정보가 부족', reply)

    def test_invalid_input_lists_errors(self):
        result = WorkflowResult.invalid_input(['시작일이 종료일보다 뒤입니다.'])
        reply = build_reply_from_result(result, workflow_key='date_calculation')
        self.assertIn('올바르지 않습니다', reply)
        self.assertIn('시작일이 종료일보다 뒤', reply)

    def test_unsupported_passes_reason_through(self):
        # Phase 6-3: reason 이 있으면 그대로 pass-through.
        result = WorkflowResult.unsupported('등록되지 않은 workflow_key 입니다: \'ghost\'')
        reply = build_reply_from_result(result, workflow_key='ghost')
        self.assertEqual(reply, "등록되지 않은 workflow_key 입니다: 'ghost'")

    def test_unsupported_without_reason_uses_default(self):
        result = WorkflowResult(status=WorkflowStatus.UNSUPPORTED)
        reply = build_reply_from_result(result, workflow_key='')
        self.assertIn('지원하는 workflow', reply)

    # Phase 6-3: NOT_FOUND / UPSTREAM_ERROR 가 각기 다른 기본 문구를 돌려주는지.

    def test_not_found_passes_reason_through(self):
        result = WorkflowResult.not_found('질문에 맞는 표를 찾지 못했습니다. 관련 문서가 업로드되어 있는지 확인해 주세요.')
        reply = build_reply_from_result(result, workflow_key='table_lookup')
        self.assertEqual(reply, '질문에 맞는 표를 찾지 못했습니다. 관련 문서가 업로드되어 있는지 확인해 주세요.')

    def test_not_found_without_reason_uses_default(self):
        result = WorkflowResult(status=WorkflowStatus.NOT_FOUND)
        reply = build_reply_from_result(result, workflow_key='table_lookup')
        self.assertIn('자료를 찾지 못했습니다', reply)
        self.assertNotIn('지원하지 않는', reply)

    def test_upstream_error_passes_reason_through(self):
        result = WorkflowResult.upstream_error('표 해석 중 일시적인 오류가 발생했습니다.')
        reply = build_reply_from_result(result, workflow_key='table_lookup')
        self.assertIn('일시적', reply)

    def test_upstream_error_without_reason_uses_default(self):
        result = WorkflowResult(status=WorkflowStatus.UPSTREAM_ERROR)
        reply = build_reply_from_result(result, workflow_key='table_lookup')
        self.assertIn('잠시 후 다시 시도', reply)

    # Phase 6-3: table_lookup OK 포맷.

    def test_ok_table_lookup_full_metadata(self):
        result = WorkflowResult.ok(
            '500만원',
            details={
                'matched_row': '본인 상',
                'matched_column': '경조금',
                'source_document': '경조사_규정.pdf',
            },
        )
        reply = build_reply_from_result(result, workflow_key='table_lookup')
        self.assertIn('본인 상 · 경조금: 500만원', reply)
        self.assertIn('(출처: 경조사_규정.pdf)', reply)

    def test_ok_table_lookup_without_metadata_shows_value_only(self):
        result = WorkflowResult.ok('42')
        reply = build_reply_from_result(result, workflow_key='table_lookup')
        self.assertEqual(reply, '42')

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

    def test_unsupported_shows_reason(self):
        result = WorkflowResult.unsupported('등록되지 않은 workflow_key 입니다: \'ghost\'')
        reply = build_reply_from_result(result, workflow_key='ghost')
        self.assertIn('지원하지 않는', reply)
        self.assertIn('ghost', reply)

    def test_unsupported_without_reason_uses_default(self):
        result = WorkflowResult(status=WorkflowStatus.UNSUPPORTED)
        reply = build_reply_from_result(result, workflow_key='')
        self.assertEqual(reply, '현재 지원하지 않는 작업입니다.')

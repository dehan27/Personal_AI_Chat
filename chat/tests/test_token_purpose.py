"""Phase 8-2 — `chat.services.token_purpose` 상수 / 멤버십 / validate 단위 테스트."""

from django.test import SimpleTestCase

from chat.services import token_purpose as tp


class AllPurposesMembershipTests(SimpleTestCase):
    """`ALL_PURPOSES` 가 모든 known 상수를 포함."""

    def test_known_six_purposes_are_members(self):
        # known 6종: single_shot_answer / query_rewriter / workflow_extractor /
        # workflow_table_lookup / agent_step / agent_final.
        self.assertIn(tp.PURPOSE_SINGLE_SHOT_ANSWER, tp.ALL_PURPOSES)
        self.assertIn(tp.PURPOSE_QUERY_REWRITER, tp.ALL_PURPOSES)
        self.assertIn(tp.PURPOSE_WORKFLOW_EXTRACTOR, tp.ALL_PURPOSES)
        self.assertIn(tp.PURPOSE_WORKFLOW_TABLE_LOOKUP, tp.ALL_PURPOSES)
        self.assertIn(tp.PURPOSE_AGENT_STEP, tp.ALL_PURPOSES)
        self.assertIn(tp.PURPOSE_AGENT_FINAL, tp.ALL_PURPOSES)

    def test_unknown_is_member(self):
        self.assertIn(tp.PURPOSE_UNKNOWN, tp.ALL_PURPOSES)

    def test_total_count_is_seven(self):
        # 6 known + 1 unknown.
        self.assertEqual(len(tp.ALL_PURPOSES), 7)


class ValidatePurposeTests(SimpleTestCase):
    """`validate_purpose` 의 정상 / 오타 / 빈문자열 / 빈 None 입력 처리."""

    def test_known_purpose_returned_as_is(self):
        for purpose in tp.ALL_PURPOSES:
            self.assertEqual(tp.validate_purpose(purpose), purpose)

    def test_unknown_string_demoted_to_unknown(self):
        # 호출부 오타 같은 케이스는 'unknown' 으로 절감되어 데이터 오염 차단.
        self.assertEqual(tp.validate_purpose('agent_step_typo'), tp.PURPOSE_UNKNOWN)

    def test_empty_string_demoted_to_unknown(self):
        self.assertEqual(tp.validate_purpose(''), tp.PURPOSE_UNKNOWN)

"""Phase 7-1 agent.tools_builtin 단위 테스트.

세 도구가 실제 모듈로 위임되는지 mock 으로 확인. summarize 출력의 한국어 요약이
LLM 다음 iteration 컨텍스트에 그대로 실릴 모양인지 검증.

Phase 7-3 부터 `_focus_window` / `_tokenize_query` helper 의 격리 단위 테스트
(`FocusWindowTests`) 와 retrieve summary 의 windowing 회귀 테스트가 추가됨.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from chat.services.agent import tools
from chat.services.agent.tools_builtin import (
    _earliest_match,
    _focus_window,
    _has_meaningful_match,
    _tokenize_query,
)
from chat.workflows.core import WorkflowResult


class BuiltinToolsRegistryTests(SimpleTestCase):
    def test_three_tools_registered_on_package_import(self):
        # `chat.services.agent` 가 import 된 시점에 자동 등록.
        names = [t.name for t in tools.all_entries()]
        self.assertIn('retrieve_documents', names)
        self.assertIn('find_canonical_qa', names)
        self.assertIn('run_workflow', names)

    def test_run_workflow_is_raw_mode(self):
        tool = tools.get('run_workflow')
        self.assertIsNone(tool.input_schema)

    def test_retrieval_tools_use_query_text_schema(self):
        for name in ('retrieve_documents', 'find_canonical_qa'):
            schema = tools.get(name).input_schema
            self.assertIsNotNone(schema)
            self.assertIn('query', schema)
            self.assertEqual(schema['query'].type, 'text')


class RetrieveDocumentsToolTests(SimpleTestCase):
    def test_delegates_to_single_shot_retrieval(self):
        # Phase 7-4: query 의 longest meaningful token (`경조금` 3자) 이 청크에
        # 있어야 is_failure=False — failure_check 정책상 의미 매치 필요.
        chunk = SimpleNamespace(
            document_name='경조사_규정.pdf',
            content='본인 상 경조금 500만원 표가 있는 청크 본문',
        )
        with patch(
            'chat.services.agent.tools_builtin._retrieve',
            return_value=[chunk, chunk, chunk],
        ) as mocked:
            obs = tools.call('retrieve_documents', {'query': '본인 상 경조금'})
        mocked.assert_called_once_with('본인 상 경조금')
        self.assertFalse(obs.is_failure)
        self.assertIn('3건', obs.summary)
        self.assertIn('경조사_규정.pdf', obs.summary)

    def test_zero_results_summary(self):
        # Phase 7-4: 0건 retrieve 도 failure_check 가 True 반환 → is_failure=True,
        # failure_kind='low_relevance' (no useful evidence 신호 통합).
        with patch(
            'chat.services.agent.tools_builtin._retrieve',
            return_value=[],
        ):
            obs = tools.call('retrieve_documents', {'query': '없는주제'})
        self.assertTrue(obs.is_failure)
        self.assertEqual(obs.failure_kind, 'low_relevance')
        self.assertIn('0건', obs.summary)

    def test_summary_exposes_top_chunk_contents_for_llm(self):
        """7-2 smoke 회귀: 첫 청크 80자만 노출하면 LLM 이 비교 답변을 못 만든다."""
        chunks = [
            SimpleNamespace(
                document_name='복리후생.pdf',
                content='본인 결혼 100만원 자녀 결혼 50만원 형제 결혼 30만원 ' * 2,
            ),
            SimpleNamespace(
                document_name='취업규칙.pdf',
                content='유급 휴가 본인 결혼 5일 자녀 결혼 1일',
            ),
        ]
        with patch(
            'chat.services.agent.tools_builtin._retrieve',
            return_value=chunks,
        ):
            obs = tools.call('retrieve_documents', {'query': '결혼 경조금'})
        # 두 청크의 출처가 모두 노출돼야 비교가 가능.
        self.assertIn('복리후생.pdf', obs.summary)
        self.assertIn('취업규칙.pdf', obs.summary)
        # 실제 값(100만원, 50만원, 5일)이 요약에 들어가야 LLM 이 답을 만든다.
        self.assertIn('100만원', obs.summary)
        self.assertIn('50만원', obs.summary)
        self.assertIn('5일', obs.summary)
        # 컨텍스트 폭주 방지 — observation 1500자 캡 안 (Phase 7-2 smoke 후 상향).
        self.assertLessEqual(len(obs.summary), 1500)

    def test_summary_truncates_top_chunks_per_chunk_limit(self):
        long_content = '본인 결혼 ' + ('가' * 500)
        chunks = [
            SimpleNamespace(document_name='a.pdf', content=long_content)
        ]
        with patch(
            'chat.services.agent.tools_builtin._retrieve',
            return_value=chunks,
        ):
            obs = tools.call('retrieve_documents', {'query': 'q'})
        # 청크 본문이 길면 잘려 '…' 표시.
        self.assertIn('…', obs.summary)

    def test_summary_caps_at_top_n_chunks(self):
        many = [
            SimpleNamespace(document_name=f'doc{i}.pdf', content=f'내용{i}')
            for i in range(7)
        ]
        with patch(
            'chat.services.agent.tools_builtin._retrieve',
            return_value=many,
        ):
            obs = tools.call('retrieve_documents', {'query': 'q'})
        # 처음 3개는 노출, 나머지는 '이하 N건 생략' 으로 요약.
        self.assertIn('doc0.pdf', obs.summary)
        self.assertIn('doc2.pdf', obs.summary)
        self.assertIn('이하 4건 생략', obs.summary)
        self.assertNotIn('doc6.pdf', obs.summary)

    # ---------- Phase 7-3: query-focused windowing 회귀 ----------

    def test_summary_exposes_value_past_first_400_chars_when_keyword_in_window(self):
        """7-3 본 목적: 답이 401자+ 위치에 있을 때 windowing 으로 노출."""
        # 청크: 0~599자 채움, 600~602자 키워드 '경조금', 603~607자 값 '[VAL]', ...
        content = (
            'a' * 600
            + '경조금'
            + '[VAL]'
            + 'b' * 392
        )
        chunks = [SimpleNamespace(document_name='복리후생.pdf', content=content)]
        with patch(
            'chat.services.agent.tools_builtin._retrieve',
            return_value=chunks,
        ):
            obs = tools.call('retrieve_documents', {'query': '결혼 경조금'})
        # 7-2 fallback (첫 400자) 으론 '[VAL]' 이 안 나왔지만, 7-3 windowing 으론 나옴.
        self.assertIn('[VAL]', obs.summary)
        self.assertIn('경조금', obs.summary)

    def test_summary_falls_back_to_first_n_when_keyword_not_found(self):
        """미매치 시 7-2 와 byte-identical fallback."""
        content = 'a' * 1000
        chunks = [SimpleNamespace(document_name='무관.pdf', content=content)]
        with patch(
            'chat.services.agent.tools_builtin._retrieve',
            return_value=chunks,
        ):
            obs = tools.call('retrieve_documents', {'query': '결혼 경조금'})
        # 첫 400자 + '…' 이 summary 안에 들어가야 함.
        self.assertIn('a' * 400 + '…', obs.summary)

    def test_summary_matches_korean_keyword_inside_chunk(self):
        """한국어 키워드가 청크 안쪽 (length 너머) 에서 매치돼 윈도우가 이동."""
        content = 'b' * 500 + '본인 결혼 100만원' + 'c' * 480
        chunks = [SimpleNamespace(document_name='복리후생.pdf', content=content)]
        with patch(
            'chat.services.agent.tools_builtin._retrieve',
            return_value=chunks,
        ):
            obs = tools.call('retrieve_documents', {'query': '본인 결혼'})
        self.assertIn('100만원', obs.summary)

    def test_summary_uses_longer_token_position_over_earlier_short_match(self):
        """P2-3 회귀 가드: 짧은 토큰이 청크 앞부분에 있어도 긴 토큰 위치가 윈도우 중심."""
        content = (
            'b' * 50
            + '비교'         # 짧은 토큰, 50자 위치
            + 'c' * 548
            + '경조금'        # 긴 토큰, 600자 위치
            + '[VAL]'        # 603자
            + 'd' * 392
        )
        chunks = [SimpleNamespace(document_name='복리후생.pdf', content=content)]
        with patch(
            'chat.services.agent.tools_builtin._retrieve',
            return_value=chunks,
        ):
            obs = tools.call('retrieve_documents', {'query': '비교 결혼 경조금'})
        # '경조금' 위치 윈도우라 [VAL] 이 들어와야 한다 (짧은 토큰 '비교' 위치였다면 못 봄).
        self.assertIn('[VAL]', obs.summary)
        self.assertIn('경조금', obs.summary)


class FindCanonicalQAToolTests(SimpleTestCase):
    def test_delegates_to_qa_cache(self):
        hit = SimpleNamespace(
            qa_id=1, question='경조사 규정', answer='...', similarity=0.92,
        )
        with patch(
            'chat.services.agent.tools_builtin._qa_cache_find',
            return_value=[hit],
        ) as mocked:
            obs = tools.call('find_canonical_qa', {'query': '경조사'})
        mocked.assert_called_once_with('경조사')
        self.assertFalse(obs.is_failure)
        self.assertIn('similarity=0.920', obs.summary)


class RunWorkflowToolTests(SimpleTestCase):
    def test_delegates_to_dispatch_with_arguments(self):
        ok = WorkflowResult.ok(31, details={'unit_label': '일'})
        with patch(
            'chat.services.agent.tools_builtin._workflow_dispatch.run',
            return_value=ok,
        ) as mocked:
            obs = tools.call('run_workflow', {
                'workflow_key': 'date_calculation',
                'input': {'start': '2025-01-01', 'end': '2025-02-01'},
            })
        mocked.assert_called_once_with(
            'date_calculation',
            {'start': '2025-01-01', 'end': '2025-02-01'},
        )
        self.assertFalse(obs.is_failure)
        self.assertIn('status=ok', obs.summary)
        self.assertIn('31', obs.summary)

    def test_unknown_workflow_key_surfaces_unsupported_in_observation(self):
        # raw mode 라 schema 검증 단계는 통과 — dispatch 가 자체적으로
        # WorkflowResult.unsupported(...) 를 돌려준다.
        unsupported = WorkflowResult.unsupported(
            "등록되지 않은 workflow_key 입니다: 'ghost'"
        )
        with patch(
            'chat.services.agent.tools_builtin._workflow_dispatch.run',
            return_value=unsupported,
        ):
            obs = tools.call('run_workflow', {'workflow_key': 'ghost'})
        self.assertFalse(obs.is_failure)  # 호출 자체는 정상.
        self.assertIn('status=unsupported', obs.summary)
        self.assertIn('ghost', obs.summary)

    def test_workflow_missing_input_surfaces_in_observation(self):
        missing = WorkflowResult.missing_input(['start', 'end'])
        with patch(
            'chat.services.agent.tools_builtin._workflow_dispatch.run',
            return_value=missing,
        ):
            obs = tools.call('run_workflow', {
                'workflow_key': 'date_calculation',
                'input': {},
            })
        self.assertIn('missing', obs.summary)
        self.assertIn('start', obs.summary)


# ---------------------------------------------------------------------------
# Phase 7-3: query-focused snippet windowing
# ---------------------------------------------------------------------------


class TokenizeQueryTests(SimpleTestCase):
    """`_tokenize_query` — punctuation strip + len≥2 + len desc sort."""

    def test_empty_query_returns_empty_list(self):
        self.assertEqual(_tokenize_query(''), [])
        self.assertEqual(_tokenize_query('   '), [])

    def test_strips_trailing_punctuation(self):
        # `결혼?` / `"경조금"` 같은 케이스에서 양 끝 punctuation 제거.
        result = _tokenize_query('결혼? "경조금"')
        self.assertIn('결혼', result)
        self.assertIn('경조금', result)

    def test_drops_single_char_tokens(self):
        # 1자 조사/어미 ('는', '이') 제거. 2자+ 만 살아남음.
        result = _tokenize_query('가 나 다 결혼')
        self.assertEqual(result, ['결혼'])

    def test_sorts_by_length_descending(self):
        # 긴 토큰부터 → 짧은 토큰 순.
        result = _tokenize_query('비교 결혼 경조금')  # 2 / 2 / 3
        self.assertEqual(result[0], '경조금')

    def test_stable_sort_for_same_length_tokens(self):
        # 같은 길이는 입력 순서 유지 — `결혼` 이 `휴가` 보다 앞에.
        result = _tokenize_query('결혼 휴가')
        self.assertEqual(result, ['결혼', '휴가'])


class FocusWindowTests(SimpleTestCase):
    """`_focus_window` — forward-bias 윈도우 + 7-2 byte-identical 회귀 가드."""

    LENGTH = 400

    def _content(self, total_len, *, marker_pos=None, marker='[VALUE]'):
        """길이 `total_len` 의 채움 content. marker_pos 가 지정되면 그 위치에 marker 삽입."""
        filler = 'x' * total_len
        if marker_pos is None:
            return filler
        return filler[:marker_pos] + marker + filler[marker_pos + len(marker):][:total_len - marker_pos - len(marker)]

    # ---------- edge cases ----------

    def test_empty_content_returns_empty_string(self):
        self.assertEqual(_focus_window('', '결혼', length=self.LENGTH), '')

    def test_content_shorter_than_length_returns_unchanged(self):
        short = '짧은 본문'
        self.assertEqual(_focus_window(short, '결혼', length=self.LENGTH), short)

    def test_empty_query_falls_back_to_first_n_chars(self):
        long = 'a' * 1000
        result = _focus_window(long, '', length=self.LENGTH)
        self.assertEqual(result, 'a' * self.LENGTH + '…')

    def test_only_one_char_tokens_falls_back_to_first_n_chars(self):
        long = 'a' * 1000
        # 1자만 있는 query → 토큰 0 → fallback.
        result = _focus_window(long, '가 나 다', length=self.LENGTH)
        self.assertEqual(result, 'a' * self.LENGTH + '…')

    # ---------- 매치 위치별 동작 ----------

    def test_match_in_very_front_is_byte_identical_to_7_2_fallback(self):
        # earliest < length//4 (예: 50자) → 자연 클램프로 start=0 → 첫 N자 + '…'.
        content = self._content(1000, marker_pos=50, marker='결혼')
        result = _focus_window(content, '결혼', length=self.LENGTH)
        # 7-2 fallback 출력과 byte-identical: 첫 400자 + '…'
        expected_first_400 = content[:self.LENGTH] + '…'
        self.assertEqual(result, expected_first_400)
        # prefix `…` 없음 (start == 0)
        self.assertFalse(result.startswith('…'))

    def test_keyword_at_350_with_value_at_450_window_includes_value(self):
        # P2 추가 지적의 본 케이스: 키워드는 length 안 (350자), 값은 length 너머 (450자).
        # 7-2 fallback 으로는 값을 못 봤지만 7-3 forward-bias 로 윈도우가 이동해
        # 값을 포함해야 함.
        content = (
            'a' * 350           # 0~349: 채움
            + '결혼'             # 350~351: 키워드
            + 'b' * 98           # 352~449: 채움
            + '[VAL]'            # 450~454: 값
            + 'c' * 545          # 455~999: 채움
        )
        self.assertEqual(len(content), 1000)
        result = _focus_window(content, '결혼', length=self.LENGTH)
        # 값이 윈도우에 포함됐는지 — 본 PR 의 핵심 검증.
        self.assertIn('[VAL]', result)
        # 7-2 byte-identical 은 아님 — 윈도우가 이동.
        self.assertNotEqual(result, content[:self.LENGTH] + '…')

    def test_match_past_length_window_centers_around_match(self):
        # earliest ≥ length (예: 600자) — windowing 의 본 무대.
        content = (
            'a' * 600           # 0~599: 채움
            + '경조금'           # 600~602: 키워드
            + 'b' * 397          # 603~999: 채움
        )
        self.assertEqual(len(content), 1000)
        result = _focus_window(content, '경조금', length=self.LENGTH)
        # 윈도우에 키워드 포함.
        self.assertIn('경조금', result)
        # prefix '…' 있음 — start > 0.
        self.assertTrue(result.startswith('…'))

    def test_match_near_end_anchors_window_to_content_end(self):
        # 매치가 content 끝 근처 (예: 950자) → 윈도우 끝이 1000 에 달라붙고 길이 보존.
        content = 'a' * 950 + '결혼' + 'b' * 48
        self.assertEqual(len(content), 1000)
        result = _focus_window(content, '결혼', length=self.LENGTH)
        # 끝에 닿았으므로 suffix '…' 없음.
        self.assertFalse(result.endswith('…'))
        self.assertIn('결혼', result)

    def test_no_match_falls_back_to_first_n_chars(self):
        long = 'a' * 1000
        result = _focus_window(long, '결혼', length=self.LENGTH)
        # 미매치 → 첫 400자 + '…' (7-2 fallback 과 동일).
        self.assertEqual(result, 'a' * self.LENGTH + '…')

    def test_case_insensitive_match(self):
        # query 'MAX' 가 content 의 'max' 와 매치.
        content = 'a' * 600 + 'max' + 'b' * 397
        result = _focus_window(content, 'MAX', length=self.LENGTH)
        self.assertIn('max', result.lower())
        self.assertTrue(result.startswith('…'))

    # ---------- P2-3: 토큰 우선순위 정책 ----------

    def test_longer_token_takes_precedence_over_earlier_short_token(self):
        # query 에 '비교' (2자) + '경조금' (3자). 청크의 '비교' 가 50자 위치, '경조금' 이
        # 600자 위치. 윈도우 중심은 길이 우선 정책에 의해 600 (`경조금`) 이어야 함.
        content = (
            'a' * 50            # 0~49
            + '비교'             # 50~51
            + 'b' * 548          # 52~599
            + '경조금'            # 600~602
            + 'c' * 397          # 603~999
        )
        self.assertEqual(len(content), 1000)
        result = _focus_window(content, '비교 결혼 경조금', length=self.LENGTH)
        self.assertIn('경조금', result)
        # `비교` 위치가 50 (< length//4 = 100) 이라면 `비교` 매치 시 byte-identical
        # 첫 N자 였을 텐데, 긴 토큰 우선이라 `경조금` 위치가 윈도우 중심 → prefix '…'.
        self.assertTrue(result.startswith('…'))

    def test_punctuation_stripped_from_query_tokens(self):
        # query '결혼?' 의 '?' 는 strip 되어 '결혼' 매치.
        content = 'a' * 600 + '결혼' + 'b' * 397
        result = _focus_window(content, '결혼?', length=self.LENGTH)
        self.assertIn('결혼', result)
        self.assertTrue(result.startswith('…'))


# ---------------------------------------------------------------------------
# Phase 7-4: _earliest_match + _has_meaningful_match
# ---------------------------------------------------------------------------


class EarliestMatchTests(SimpleTestCase):
    """`_earliest_match` — windowing 용 매치 위치. 모든 토큰 후보 (low-signal 포함)."""

    def test_match_returns_position(self):
        content = 'a' * 100 + '경조금' + 'b' * 100
        self.assertEqual(_earliest_match(content, '경조금'), 100)

    def test_no_match_returns_minus_one(self):
        self.assertEqual(_earliest_match('a' * 200, '결혼'), -1)

    def test_empty_inputs_return_minus_one(self):
        self.assertEqual(_earliest_match('', '결혼'), -1)
        self.assertEqual(_earliest_match('content', ''), -1)
        self.assertEqual(_earliest_match('content', '가'), -1)  # all 1자 → tokens 0

    def test_longer_token_matched_first(self):
        # query 정렬: 경조금(3) > 비교(2). 경조금 위치가 우선 반환.
        content = 'a' * 50 + '비교' + 'b' * 548 + '경조금' + 'c' * 397
        self.assertEqual(_earliest_match(content, '비교 경조금'), 600)


class HasMeaningfulMatchTests(SimpleTestCase):
    """`_has_meaningful_match` — strict 정책 (longest meaningful token tier 매치 필요).

    P2-1 의 핵심 회귀 가드: 짧은 의미 토큰만 매치되는 케이스 False.
    """

    def test_longest_meaningful_match_is_relevant(self):
        # query "결혼 경조금 비교": meaningful=[경조금(3), 결혼(2)], longest=경조금.
        content = 'a' * 100 + '경조금 50만원' + 'b' * 100
        self.assertTrue(_has_meaningful_match(content, '결혼 경조금 비교'))

    def test_only_short_meaningful_token_matched_returns_false(self):
        # P2-1 핵심: query "우주여행 비용 비교" 의 longest=우주여행(4). "비용"(2자)
        # 만 매치돼도 False — 무한 retrieve 회로 차단의 핵심.
        content = '경조사 비용 항목 표' + 'a' * 200
        self.assertFalse(_has_meaningful_match(content, '우주여행 비용 비교'))

    def test_tied_max_len_either_match_returns_true(self):
        # query "결혼 휴가": 둘 다 2자, longest_tier=[결혼, 휴가]. 한쪽만 매치돼도 OK.
        content = '본인 결혼 시 5일' + 'a' * 100
        self.assertTrue(_has_meaningful_match(content, '결혼 휴가'))
        content2 = '자녀 휴가 신청' + 'a' * 100
        self.assertTrue(_has_meaningful_match(content2, '결혼 휴가'))

    def test_only_low_signal_token_matched_returns_false(self):
        # query "결혼 비교" 의 meaningful=[결혼]. 청크에 "비교" 만 있으면 False
        # ("비교" 는 low-signal 이라 meaningful 토큰에서 제외됨).
        content = '경조사 항목 비교 표' + 'a' * 100
        self.assertFalse(_has_meaningful_match(content, '결혼 비교'))

    def test_empty_query_returns_false(self):
        self.assertFalse(_has_meaningful_match('content', ''))

    def test_all_low_signal_query_returns_false(self):
        # query 자체가 모두 low-signal → 정보량 부족.
        self.assertFalse(_has_meaningful_match('어떤 비교 자료', '비교 알려줘'))


class RetrieveSummaryRelevanceMarkerTests(SimpleTestCase):
    """`_summarize_retrieve` 의 [관련성 낮음] / 머리 마커 회귀 — Phase 7-4."""

    def test_all_hits_meaningful_no_markers(self):
        chunks = [
            SimpleNamespace(document_name='복리후생.pdf', content='경조금 50만원 표'),
        ]
        with patch(
            'chat.services.agent.tools_builtin._retrieve',
            return_value=chunks,
        ):
            obs = tools.call('retrieve_documents', {'query': '경조금'})
        self.assertNotIn('[관련성 낮음]', obs.summary)
        self.assertNotIn('[query 핵심 토큰 매치 없음', obs.summary)

    def test_all_hits_low_signal_only_adds_head_and_per_chunk_markers(self):
        # 모든 hit 가 longest meaningful 미매치 → 머리 + per-chunk 마커.
        chunks = [
            SimpleNamespace(document_name='무관.pdf', content='경조사 비용 항목'),
            SimpleNamespace(document_name='무관2.pdf', content='다른 비용 표'),
        ]
        with patch(
            'chat.services.agent.tools_builtin._retrieve',
            return_value=chunks,
        ):
            obs = tools.call('retrieve_documents', {'query': '우주여행 비용 비교'})
        self.assertIn('[query 핵심 토큰 매치 없음', obs.summary)
        # per-chunk 마커도 모두 부착.
        self.assertEqual(obs.summary.count('[관련성 낮음]'), 2)

    def test_partial_meaningful_match_per_chunk_marker_only(self):
        # 일부 hit 만 의미 매치 → 미매치 청크에만 [관련성 낮음] / 머리 마커는 없음.
        # query "결혼 휴가" 의 longest_tier=[결혼, 휴가] (둘 다 2자) — 한쪽만 매치돼도 True.
        chunks = [
            SimpleNamespace(document_name='경조사.pdf', content='본인 결혼 100만원'),
            SimpleNamespace(document_name='무관.pdf', content='어제 회의록'),
        ]
        with patch(
            'chat.services.agent.tools_builtin._retrieve',
            return_value=chunks,
        ):
            obs = tools.call('retrieve_documents', {'query': '결혼 휴가'})
        self.assertNotIn('[query 핵심 토큰 매치 없음', obs.summary)
        # 미매치 청크 한 개에만 마커.
        self.assertEqual(obs.summary.count('[관련성 낮음]'), 1)


class RetrieveFailureCheckTests(SimpleTestCase):
    """`_retrieve_failure_check` 회귀 — Phase 7-4 (failure_kind='low_relevance' 마킹)."""

    def test_all_low_relevance_returns_is_failure(self):
        chunks = [
            SimpleNamespace(document_name='무관.pdf', content='어제 회의록')
            for _ in range(2)
        ]
        with patch(
            'chat.services.agent.tools_builtin._retrieve',
            return_value=chunks,
        ):
            obs = tools.call('retrieve_documents', {'query': '우주여행 비용 비교'})
        self.assertTrue(obs.is_failure)
        self.assertEqual(obs.failure_kind, 'low_relevance')

    def test_partial_meaningful_returns_success(self):
        chunks = [
            SimpleNamespace(document_name='경조사.pdf', content='본인 결혼 100만원'),
            SimpleNamespace(document_name='무관.pdf', content='어제 회의록'),
        ]
        with patch(
            'chat.services.agent.tools_builtin._retrieve',
            return_value=chunks,
        ):
            obs = tools.call('retrieve_documents', {'query': '결혼 휴가'})
        self.assertFalse(obs.is_failure)
        self.assertIsNone(obs.failure_kind)

    def test_zero_hit_treated_as_low_relevance(self):
        # P3: 0건도 failure_kind='low_relevance' 로 처리.
        with patch(
            'chat.services.agent.tools_builtin._retrieve',
            return_value=[],
        ):
            obs = tools.call('retrieve_documents', {'query': '뭐든'})
        self.assertTrue(obs.is_failure)
        self.assertEqual(obs.failure_kind, 'low_relevance')

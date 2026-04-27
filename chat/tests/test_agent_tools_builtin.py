"""Phase 7-1 agent.tools_builtin 단위 테스트.

세 도구가 실제 모듈로 위임되는지 mock 으로 확인. summarize 출력의 한국어 요약이
LLM 다음 iteration 컨텍스트에 그대로 실릴 모양인지 검증.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from chat.services.agent import tools
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
        chunk = SimpleNamespace(
            document_name='경조사_규정.pdf',
            content='본인 상 500만원 표가 있는 청크 본문',
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
        with patch(
            'chat.services.agent.tools_builtin._retrieve',
            return_value=[],
        ):
            obs = tools.call('retrieve_documents', {'query': '없는주제'})
        self.assertFalse(obs.is_failure)
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

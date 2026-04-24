"""Phase 6-3 table_lookup workflow 단위 테스트 (스켈레톤 단계)."""

from django.test import SimpleTestCase

from chat.workflows.core import WorkflowStatus, run_workflow
from chat.workflows.domains.general.table_lookup import (
    INPUT_SCHEMA,
    TableLookupWorkflow,
    WORKFLOW_KEY,
)


class TableLookupScaffoldTests(SimpleTestCase):
    """retrieval / LLM 연결 이전 — 입력 검증 · 등록 동작만 확인."""

    def _run(self, raw):
        return run_workflow(TableLookupWorkflow(), raw)

    def test_query_required(self):
        r = self._run({})
        self.assertEqual(r.status, WorkflowStatus.MISSING_INPUT)
        self.assertIn('query', r.missing_fields)

    def test_blank_query_is_missing(self):
        r = self._run({'query': '  '})
        self.assertEqual(r.status, WorkflowStatus.MISSING_INPUT)

    def test_placeholder_execute_returns_query(self):
        # 다음 커밋에서 대체될 임시 동작 — 스켈레톤 단계에서만 유효.
        r = self._run({'query': '표에서 본인 상 경조금'})
        self.assertEqual(r.status, WorkflowStatus.OK)
        self.assertEqual(r.value, '표에서 본인 상 경조금')
        self.assertTrue(r.details.get('placeholder'))

    def test_registered_with_text_query_schema(self):
        from chat.workflows.domains import registry
        self.assertTrue(registry.has(WORKFLOW_KEY))
        entry = registry.get(WORKFLOW_KEY)
        self.assertEqual(entry.input_schema, INPUT_SCHEMA)
        self.assertEqual(entry.input_schema['query'].type, 'text')

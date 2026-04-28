"""Phase 8-2 — 7 호출 사이트가 정확한 purpose 를 전달하는지 검증.

각 호출자의 실제 LLM 호출 / DB 부수 작업은 mock 으로 격리하고, 본 테스트는
`record_token_usage` 가 받는 keyword 인자만 본다. 이게 깨지면 BO 분해 집계의
의미가 없어지는 회귀라 1 사이트 1 case 가 원칙.

본 파일은 Step 3 (single_shot pipeline / workflow node / agent_node / table_lookup)
6 cases + Step 4 (agent.react step / final 분기) 3 cases = 총 9 cases 를 다룬다.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from chat.services import token_purpose as tp


class _UsageStub:
    prompt_tokens = 10
    completion_tokens = 5
    total_tokens = 15


# ---------------------------------------------------------------------------
# single_shot pipeline — rewriter + answer
# ---------------------------------------------------------------------------


class SingleShotPipelinePurposeTests(SimpleTestCase):
    """`single_shot/pipeline.py` 의 두 record_token_usage 호출 검증."""

    def _patch_common(self, *, rewriter_history=False):
        # rewriter 가 history 있을 때만 usage 반환 — pipeline 의 if 분기 검증.
        rewriter_return = (
            ('rewritten', _UsageStub(), 'gpt-4o-mini')
            if rewriter_history
            else ('original', None, None)
        )
        return [
            patch(
                'chat.services.single_shot.pipeline.rewrite_query_with_history',
                return_value=rewriter_return,
            ),
            patch(
                'chat.services.single_shot.pipeline.retrieve_documents',
                return_value=[],
            ),
            patch(
                'chat.services.single_shot.pipeline.find_canonical_qa',
                return_value=[],
            ),
            patch(
                'chat.services.single_shot.pipeline.resolve_cache_hit',
                return_value=None,
            ),
            patch(
                'chat.services.single_shot.pipeline.build_single_shot_messages',
                return_value=[{'role': 'user', 'content': 'q'}],
            ),
            patch(
                'chat.services.single_shot.pipeline.run_chat_completion',
                return_value=('reply text', _UsageStub(), 'gpt-4o-mini'),
            ),
        ]

    def test_answer_call_records_single_shot_answer(self):
        from chat.services.single_shot.pipeline import run_single_shot

        with patch(
            'chat.services.single_shot.pipeline.record_token_usage',
        ) as record:
            for ctx in self._patch_common(rewriter_history=False):
                ctx.start()
            try:
                run_single_shot('Q', history=None)
            finally:
                # patches 모두 stop.
                patch.stopall()

        # rewriter 호출 0 (history 없음) → record_token_usage 한 번만.
        self.assertEqual(record.call_count, 1)
        kwargs = record.call_args.kwargs
        self.assertEqual(kwargs.get('purpose'), tp.PURPOSE_SINGLE_SHOT_ANSWER)

    def test_rewriter_call_records_query_rewriter(self):
        from chat.services.single_shot.pipeline import run_single_shot

        with patch(
            'chat.services.single_shot.pipeline.record_token_usage',
        ) as record:
            for ctx in self._patch_common(rewriter_history=True):
                ctx.start()
            try:
                run_single_shot('Q', history=[{'role': 'user', 'content': 'prev'}])
            finally:
                patch.stopall()

        # rewriter 1 + answer 1 = 2 회.
        self.assertEqual(record.call_count, 2)
        purposes = [c.kwargs.get('purpose') for c in record.call_args_list]
        self.assertEqual(purposes[0], tp.PURPOSE_QUERY_REWRITER)
        self.assertEqual(purposes[1], tp.PURPOSE_SINGLE_SHOT_ANSWER)


# ---------------------------------------------------------------------------
# workflow_node — rewriter (text-schema 게이트) + extractor
# ---------------------------------------------------------------------------


class WorkflowNodePurposeTests(SimpleTestCase):
    """`graph/nodes/workflow.py` 의 rewriter / extractor record 검증."""

    def _patch_workflow(self, *, schema_has_text=True):
        # registry/dispatch/extractor mock — workflow_node 단위로 격리.
        from chat.workflows.core import WorkflowResult
        from chat.workflows.domains.field_spec import FieldSpec

        if schema_has_text:
            schema = {'q': FieldSpec(type='text', required=True)}
        else:
            schema = {'unit': FieldSpec(type='enum', required=False, enum_values={'days': ()})}
        entry = SimpleNamespace(input_schema=schema)

        return {
            'registry_has': patch(
                'chat.graph.nodes.workflow.registry.has', return_value=True,
            ),
            'registry_get': patch(
                'chat.graph.nodes.workflow.registry.get', return_value=entry,
            ),
            'rewriter': patch(
                'chat.graph.nodes.workflow.rewrite_query_with_history',
                return_value=('rewritten', _UsageStub(), 'gpt-4o-mini'),
            ),
            'extractor': patch(
                'chat.graph.nodes.workflow.extract_workflow_input',
                return_value=({'q': 'x'}, _UsageStub(), 'gpt-4o-mini'),
            ),
            'dispatch': patch(
                'chat.graph.nodes.workflow.dispatch.run',
                return_value=WorkflowResult.ok(value='answer'),
            ),
            'reply': patch(
                'chat.graph.nodes.workflow.build_reply_from_result',
                return_value='answer reply',
            ),
        }

    def test_text_schema_records_rewriter_and_extractor(self):
        from chat.graph.nodes.workflow import workflow_node

        with patch(
            'chat.graph.nodes.workflow.record_token_usage',
        ) as record:
            patches = self._patch_workflow(schema_has_text=True)
            for ctx in patches.values():
                ctx.start()
            try:
                workflow_node({
                    'workflow_key': 'table_lookup',
                    'question': 'Q',
                    'history': [{'role': 'user', 'content': 'prev'}],
                    'workflow_input': None,
                })
            finally:
                patch.stopall()

        # rewriter + extractor = 2 회.
        self.assertEqual(record.call_count, 2)
        purposes = [c.kwargs.get('purpose') for c in record.call_args_list]
        self.assertEqual(purposes[0], tp.PURPOSE_QUERY_REWRITER)
        self.assertEqual(purposes[1], tp.PURPOSE_WORKFLOW_EXTRACTOR)

    def test_non_text_schema_records_only_extractor(self):
        # _schema_needs_retrieval=False → rewriter skip → extractor 만.
        from chat.graph.nodes.workflow import workflow_node

        with patch(
            'chat.graph.nodes.workflow.record_token_usage',
        ) as record:
            patches = self._patch_workflow(schema_has_text=False)
            for ctx in patches.values():
                ctx.start()
            try:
                workflow_node({
                    'workflow_key': 'date_calculation',
                    'question': 'Q',
                    'history': [{'role': 'user', 'content': 'prev'}],
                    'workflow_input': None,
                })
            finally:
                patch.stopall()

        self.assertEqual(record.call_count, 1)
        kwargs = record.call_args.kwargs
        self.assertEqual(kwargs.get('purpose'), tp.PURPOSE_WORKFLOW_EXTRACTOR)


# ---------------------------------------------------------------------------
# agent_node — rewriter
# ---------------------------------------------------------------------------


class AgentNodePurposeTests(SimpleTestCase):
    """`graph/nodes/agent.py` 의 rewriter record 검증."""

    def test_rewriter_call_records_query_rewriter(self):
        from chat.graph.nodes.agent import agent_node
        from chat.services.agent.result import AgentResult, AgentTermination
        from chat.workflows.core import WorkflowStatus

        ok = AgentResult(
            status=WorkflowStatus.OK, value='answer',
            termination=AgentTermination.FINAL_ANSWER,
        )
        with patch(
            'chat.graph.nodes.agent.record_token_usage',
        ) as record, patch(
            'chat.graph.nodes.agent.rewrite_query_with_history',
            return_value=('rewritten', _UsageStub(), 'gpt-4o-mini'),
        ), patch(
            'chat.graph.nodes.agent.run_agent', return_value=ok,
        ):
            agent_node({
                'question': 'Q',
                'history': [{'role': 'user', 'content': 'prev'}],
            })
        self.assertEqual(record.call_count, 1)
        self.assertEqual(record.call_args.kwargs.get('purpose'), tp.PURPOSE_QUERY_REWRITER)


# ---------------------------------------------------------------------------
# table_lookup — cell selection LLM
# ---------------------------------------------------------------------------


class TableLookupPurposeTests(SimpleTestCase):
    """`workflows/domains/general/table_lookup.py` 의 LLM 호출 record 검증."""

    def test_table_lookup_records_workflow_table_lookup(self):
        from chat.workflows.domains.general.table_lookup import TableLookupWorkflow

        with patch(
            'chat.workflows.domains.general.table_lookup.record_token_usage',
        ) as record, patch(
            'chat.workflows.domains.general.table_lookup.run_chat_completion',
            return_value=(
                '{"answer": "50만원", "source_document": "x.pdf", '
                '"matched_row": "본인 결혼", "matched_column": "금액"}',
                _UsageStub(), 'gpt-4o-mini',
            ),
        ), patch(
            'chat.workflows.domains.general.table_lookup.retrieve_documents',
            return_value=[
                SimpleNamespace(
                    document_name='x.pdf',
                    document_url='/m/x',
                    content='| 항목 | 금액 |\n| --- | --- |\n| 본인 결혼 | 50만원 |',
                ),
            ],
        ), patch(
            'chat.workflows.domains.general.table_lookup.load_prompt',
            return_value='[STUB]',
        ):
            TableLookupWorkflow().execute({'query': '본인 결혼'})

        self.assertEqual(record.call_count, 1)
        self.assertEqual(
            record.call_args.kwargs.get('purpose'),
            tp.PURPOSE_WORKFLOW_TABLE_LOOKUP,
        )


# ---------------------------------------------------------------------------
# agent.react — step / final 분기 (Step 4 에서 활성화)
# ---------------------------------------------------------------------------


class AgentReactStepFinalPurposeTests(SimpleTestCase):
    """Phase 8-2 Step 4: agent.react 의 record_token_usage 가 action 에 따라 분기.

    Step 3 시점 (분기 미적용) 에는 'unknown' 으로 떨어지지만, Step 4 commit 후
    final_answer iteration 만 PURPOSE_AGENT_FINAL, 나머지는 PURPOSE_AGENT_STEP.
    """

    def _completion(self, *replies):
        iterator = iter(replies)

        def _side_effect(messages):
            return (next(iterator), _UsageStub(), 'gpt-4o-mini')

        return _side_effect

    def test_final_answer_iteration_records_agent_final(self):
        from chat.services.agent import react

        with patch(
            'chat.services.agent.prompts.load_prompt', return_value='[STUB]',
        ), patch(
            'chat.services.agent.react.record_token_usage',
        ) as record, patch(
            'chat.services.agent.react.run_chat_completion',
            side_effect=self._completion(
                '{"thought": "쉬움", "action": "final_answer", "answer": "ok"}',
            ),
        ):
            react.run_agent('Q', history=[])

        self.assertEqual(record.call_count, 1)
        self.assertEqual(
            record.call_args.kwargs.get('purpose'), tp.PURPOSE_AGENT_FINAL,
        )

    def test_tool_step_records_agent_step(self):
        from chat.services.agent import react, tools as agent_tools
        from chat.services.agent.tools import Tool
        from chat.workflows.domains.field_spec import FieldSpec

        snapshot = agent_tools._snapshot_for_tests()
        agent_tools._reset_for_tests()
        agent_tools.register(Tool(
            name='dummy', description='',
            input_schema={'query': FieldSpec(type='text', required=True)},
            callable=lambda args: 'ok',
            summarize=lambda r: 'summary',
        ))

        try:
            with patch(
                'chat.services.agent.prompts.load_prompt', return_value='[STUB]',
            ), patch(
                'chat.services.agent.react.record_token_usage',
            ) as record, patch(
                'chat.services.agent.react.run_chat_completion',
                side_effect=self._completion(
                    '{"thought": "1", "action": "dummy", "arguments": {"query": "x"}}',
                    '{"thought": "2", "action": "final_answer", "answer": "done"}',
                ),
            ):
                react.run_agent('Q', history=[])

            self.assertEqual(record.call_count, 2)
            purposes = [c.kwargs.get('purpose') for c in record.call_args_list]
            self.assertEqual(purposes[0], tp.PURPOSE_AGENT_STEP)
            self.assertEqual(purposes[1], tp.PURPOSE_AGENT_FINAL)
        finally:
            agent_tools._restore_for_tests(snapshot)

    def test_invalid_json_iteration_records_agent_step(self):
        # parse 실패 → action None → step 으로 분류.
        from chat.services.agent import react

        with patch(
            'chat.services.agent.prompts.load_prompt', return_value='[STUB]',
        ), patch(
            'chat.services.agent.react.record_token_usage',
        ) as record, patch(
            'chat.services.agent.react.run_chat_completion',
            side_effect=self._completion(
                'not json',
                '{"thought": "복구", "action": "final_answer", "answer": "ok"}',
            ),
        ):
            react.run_agent('Q', history=[])

        # 1차 (parse 실패) = step / 2차 (final_answer) = final.
        self.assertEqual(record.call_count, 2)
        purposes = [c.kwargs.get('purpose') for c in record.call_args_list]
        self.assertEqual(purposes[0], tp.PURPOSE_AGENT_STEP)
        self.assertEqual(purposes[1], tp.PURPOSE_AGENT_FINAL)

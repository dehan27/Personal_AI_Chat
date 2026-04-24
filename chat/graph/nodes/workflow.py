"""Workflow node — `state.workflow_key` 에 맞춰 domain workflow 를 실행.

동작 요약:
    - `workflow_key` 가 비어있거나 registry 에 등록되지 않은 값 → single_shot 폴백
      (Phase 4-1 과 동일한 응답).
    - 등록된 key → (Phase 6-3) 필요 시 history-aware rewrite →
      `workflow_input_extractor.extract(...)` 로 자연어 질문에서 입력을 뽑고,
      `dispatch.run(key, input)` 실행 → `WorkflowResult` 를 자연어 `reply` 로
      포맷해 `QueryResult` 로 반환.

Phase 별 변경 이력:
    - Phase 6-1: 최초 도입. dispatch 또는 single_shot 폴백.
    - Phase 6-2: workflow_input 을 extractor 로 자동 채움. TokenUsage 기록.
    - Phase 6-3: retrieval 이 필요한 workflow(`input_schema` 에 `'text'` 필드가
      있는 경우)에 한해 Phase 4-3 `rewrite_query_with_history` 를 먼저 돌려
      "그 표에서..." 같은 지시어 의존 후속 질문이 자립 검색어로 변환되도록.
"""

from __future__ import annotations

import logging
from typing import Mapping

from chat.graph.state import GraphState
from chat.graph.nodes.single_shot import single_shot_node
from chat.services.query_rewriter import rewrite_query_with_history
from chat.services.single_shot.postprocess import record_token_usage
from chat.services.single_shot.types import QueryResult
from chat.services.workflow_input_extractor import extract as extract_workflow_input
from chat.workflows.domains import dispatch, registry
from chat.workflows.domains.field_spec import FieldSpec
from chat.workflows.domains.reply import build_reply_from_result


logger = logging.getLogger(__name__)


def workflow_node(state: GraphState) -> dict:
    """dispatch 를 통해 workflow 를 실행하거나 single_shot 으로 폴백."""
    key = (state.get('workflow_key') or '').strip()

    if not key or not registry.has(key):
        # 폴백 — workflow 경로이지만 아직 붙일 workflow 가 없거나 key 가 비었음.
        # 기존 single_shot 응답과 동일하게 동작해서 회귀 0.
        return single_shot_node(state)

    entry = registry.get(key)
    raw_question = state.get('question') or ''
    history = state.get('history') or []

    # 외부가 미리 채워 보낸 workflow_input 이 있으면 rewriter·extractor 둘 다 스킵.
    # 단위 테스트 편의 + 향후 agent 가 workflow 를 tool 로 호출할 때 값 직접 주입 용.
    explicit_input = state.get('workflow_input')
    if explicit_input is not None:
        workflow_input = dict(explicit_input)
    else:
        # Phase 6-3: retrieval 을 돌리는 workflow 에 한해 history-aware rewrite.
        # rewriter 는 history 가 비었으면 LLM 호출 없이 원본 반환하므로 실질 비용 낮음.
        effective_question = raw_question
        if history and _schema_needs_retrieval(entry.input_schema):
            effective_question, rw_usage, rw_model = rewrite_query_with_history(
                raw_question, history,
            )
            if rw_usage and rw_model:
                record_token_usage(rw_model, rw_usage)

        workflow_input, ex_usage, ex_model = extract_workflow_input(
            question=effective_question,
            history=history,
            schema=entry.input_schema,
        )
        if ex_usage and ex_model:
            record_token_usage(ex_model, ex_usage)

    result = dispatch.run(key, workflow_input)
    reply = build_reply_from_result(result, workflow_key=key)

    logger.info(
        'workflow 실행: key=%s status=%s value=%r',
        key,
        result.status.value,
        result.value,
    )

    # QueryResult 는 single_shot pipeline 이 쓰던 반환형 — view 가 이미 이
    # 구조를 기대하므로 workflow 결과도 같은 형태로 싣는다. 출처·토큰 정보는
    # 현재 범위에서 의미가 없어 0 / [] 로 두고, chat_log_id 는 자료 기반 답변이
    # 아니라 None.
    return {
        'result': QueryResult(
            reply=reply,
            sources=[],
            total_tokens=0,
            chat_log_id=None,
        ),
    }


def _schema_needs_retrieval(schema: Mapping[str, FieldSpec]) -> bool:
    """workflow 가 retrieval-backed 인지 판정용 휴리스틱.

    현재 기준: schema 안에 `'text'` 타입 필드가 하나라도 있으면 retrieval 을
    돌릴 가능성이 큰 workflow 로 간주하고 rewriter 를 앞단에 한 번 돌린다.
    date/amount 처럼 text 필드가 없는 workflow 는 rewriter 호출 0 — 추가 비용 없음.

    Phase 6-3 에서 workflow 가 세 개라 이 휴리스틱으로 충분하지만, 나중에
    workflow 수가 늘면 `WorkflowEntry.needs_retrieval: bool` 로 공식화할 수 있다.
    """
    return any(spec.type == 'text' for spec in schema.values())

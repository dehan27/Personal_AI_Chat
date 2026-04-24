"""Workflow node — `state.workflow_key` 에 맞춰 domain workflow 를 실행.

동작 요약:
    - `workflow_key` 가 비어있거나 registry 에 등록되지 않은 값 → single_shot 폴백
      (Phase 4-1 과 동일한 응답).
    - 등록된 key → `workflow_input_extractor.extract(...)` 로 자연어 질문에서
      입력을 뽑고, `dispatch.run(key, input)` 실행 → `WorkflowResult` 를
      자연어 `reply` 로 포맷해 `QueryResult` 로 반환.

Phase 6-2 변경 사항:
    - 기존에 항상 `{}` 였던 `workflow_input` 을 extractor 가 채운다.
    - 외부가 명시적으로 `state.workflow_input` 에 값을 실어 보내면(테스트 편의)
      그걸 우선 사용하고 extractor 호출은 건너뛴다.
    - extractor 의 LLM fallback 이 탄 경우에만 `record_token_usage` 호출.
"""

import logging

from chat.graph.state import GraphState
from chat.graph.nodes.single_shot import single_shot_node
from chat.services.single_shot.postprocess import record_token_usage
from chat.services.single_shot.types import QueryResult
from chat.services.workflow_input_extractor import extract as extract_workflow_input
from chat.workflows.domains import dispatch, registry
from chat.workflows.domains.reply import build_reply_from_result


logger = logging.getLogger(__name__)


def workflow_node(state: GraphState) -> dict:
    """dispatch 를 통해 workflow 를 실행하거나 single_shot 으로 폴백."""
    key = (state.get('workflow_key') or '').strip()

    if not key or not registry.has(key):
        # 폴백 — workflow 경로이지만 아직 붙일 workflow 가 없거나 key 가 비었음.
        # 기존 single_shot 응답과 동일하게 동작해서 회귀 0.
        return single_shot_node(state)

    # 외부가 미리 채워 보낸 workflow_input 이 있으면 그걸 우선. 없을 때만
    # extractor 를 돌려 질문/history 에서 값을 뽑는다 (Phase 6-2).
    explicit_input = state.get('workflow_input')
    if explicit_input is not None:
        workflow_input = dict(explicit_input)
    else:
        entry = registry.get(key)
        workflow_input, usage, model = extract_workflow_input(
            question=state.get('question') or '',
            history=state.get('history') or [],
            schema=entry.input_schema,
        )
        if usage and model:
            record_token_usage(model, usage)

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

"""Workflow node — `state.workflow_key` 에 맞춰 domain workflow 를 실행 (Phase 6-1).

동작 요약:
    - `workflow_key` 가 비어있거나 registry 에 등록되지 않은 값 → single_shot 폴백
      (Phase 4-1 과 동일한 응답).
    - 등록된 key → `dispatch.run(key, workflow_input)` 실행 → `WorkflowResult` 를
      자연어 `reply` 문자열로 포맷해 `QueryResult` 로 실어서 반환.

Phase 6-1 은 workflow 가 **결정적 계산기** 역할만 한다 — OpenAI 호출 없음.
응답 문자열 조립은 `chat.workflows.domains.reply.build_reply_from_result` 가
맡는다 (별도 커밋).

`workflow_input` 은 현재 state 에 채워지는 경로가 없다. 즉 현재 흐름에선
대부분 `MISSING_INPUT` 으로 귀결되며, 사용자는 "어떤 값이 필요한지" 를 자연스
럽게 전달받는다. 자연어 질문에서 start/end 같은 값을 자동 추출하는 로직은
Phase 6-2 의 과제.
"""

import logging

from chat.graph.state import GraphState
from chat.graph.nodes.single_shot import single_shot_node
from chat.services.single_shot.types import QueryResult
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

    workflow_input = state.get('workflow_input') or {}
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

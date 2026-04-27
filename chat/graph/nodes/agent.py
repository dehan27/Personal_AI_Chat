"""Agent node — `ROUTE_AGENT` 가 들어왔을 때 실행되는 graph 노드 (Phase 7-2).

내부 흐름은 Phase 6-3 의 `workflow_node` 를 거울처럼 따라간다:

    1. `state.history` 가 있으면 `rewrite_query_with_history(question, history)`
       로 self-contained 검색어 생성. rewriter LLM usage 가 잡히면
       `record_token_usage` 로 기록.
    2. `run_agent(effective_question, history=history)` — Phase 7-1 의 ReAct
       loop 진입점. 반환은 `WorkflowResult`.
    3. `build_reply_from_agent_result(result)` 로 한국어 reply 문자열 생성.
    4. `QueryResult(reply=..., sources=[], total_tokens=0, chat_log_id=None)` 로
       감싸 view 가 기대하는 모양으로 반환.

설계 결정 (Plan §1):

- `state.question` 자체는 raw 사용자 입력으로 두고, rewriter 결과는 지역 변수로만
  흘려 `run_agent` 의 첫 인자로 넘긴다 — view·logger·history 가 raw 질문을 보게.
- TokenUsage 는 rewriter 호출분만 여기서 기록. agent step LLM 의 토큰 기록은
  `chat/services/agent/react.py` 안에서 이미 매 호출마다 처리됨 → 이중 기록 없음.
- rewriter 가 LLM 실패 시 원본을 반환하므로 graph 단 try/except 불필요
  (Phase 4-3 결정 그대로).
"""

from __future__ import annotations

import logging

from chat.graph.state import GraphState
from chat.services.agent.react import run_agent
from chat.services.agent.reply import build_reply_from_agent_result
from chat.services.query_rewriter import rewrite_query_with_history
from chat.services.single_shot.postprocess import record_token_usage
from chat.services.single_shot.types import QueryResult


logger = logging.getLogger(__name__)


def agent_node(state: GraphState) -> dict:
    """history-aware rewrite → run_agent → reply → QueryResult."""
    raw_question = state.get('question') or ''
    history = state.get('history') or []

    effective_question = raw_question
    if history:
        effective_question, rw_usage, rw_model = rewrite_query_with_history(
            raw_question, history,
        )
        if rw_usage and rw_model:
            try:
                record_token_usage(rw_model, rw_usage)
            except Exception as exc:                                  # noqa: BLE001
                # 토큰 기록 실패는 답변 자체를 막지 않는다 (table_lookup 패턴 동일).
                logger.warning('agent rewriter TokenUsage 기록 실패: %s', exc)

    result = run_agent(effective_question, history=history)
    reply = build_reply_from_agent_result(result)

    logger.info(
        'agent 실행: status=%s value=%r',
        result.status.value,
        result.value,
    )

    # `sources` / `total_tokens` 은 본 PR 범위 밖이라 0/[] 로 둔다.
    # 출처·도구 사용 요약 surface 는 후속 Phase 책임 (Plan §Out of Scope).
    return {
        'result': QueryResult(
            reply=reply,
            sources=[],
            total_tokens=0,
            chat_log_id=None,
        ),
    }

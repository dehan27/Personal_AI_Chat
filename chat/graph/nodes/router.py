"""Router node — 질문을 `single_shot / workflow / agent` 로 분류.

Phase 4-1 부터 실제 규칙 기반 분류기(`chat.services.question_router`)를 호출해
state.route / route_reason / matched_rules 를 채운다.

Phase 6-1 부터 `workflow_key` 가 함께 실려 내려간다. `workflow` route 의
일부 규칙에만 값이 있고, 나머지는 빈 문자열. `workflow_node` 가 등록된 key
여부를 보고 dispatch 로 보내든 single_shot 으로 폴백하든 결정한다.

`agent` route 는 Phase 7-1 시점까지 conditional edge 매핑이 single_shot 으로
폴백된 상태다. Phase 7-1 에서 `chat/services/agent/` runtime 자체는 import 가능
하지만 graph 노드(`chat/graph/nodes/agent.py`) 와 `app.py` 의 conditional
edge 교체는 Phase 7-2 의 책임. 7-2 가 머지되기 전까지 agent 키워드/RouterRule
이 매치돼도 응답은 기존 single_shot 과 동일하다.
"""

import logging

from chat.graph.routes import ROUTE_SINGLE_SHOT
from chat.graph.state import GraphState
from chat.services.question_router import route_question


logger = logging.getLogger(__name__)


def router_node(state: GraphState) -> dict:
    """state.question → RouteDecision → state.route/route_reason/matched_rules/workflow_key."""
    decision = route_question(state['question'])

    if decision.route != ROUTE_SINGLE_SHOT:
        logger.info(
            '라우팅: %s (reason=%s, rules=%s, workflow_key=%r)',
            decision.route,
            decision.reason,
            decision.matched_rules,
            decision.workflow_key,
        )

    return {
        'route': decision.route,
        'route_reason': decision.reason,
        'matched_rules': list(decision.matched_rules),
        'workflow_key': decision.workflow_key,
    }

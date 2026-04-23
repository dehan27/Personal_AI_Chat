"""Router node — 질문을 `single_shot / workflow / agent` 로 분류.

Phase 4-1 부터 실제 규칙 기반 분류기(`chat.services.question_router`)를 호출해
state.route / route_reason / matched_rules 를 채운다. workflow·agent 노드는
아직 없으므로 graph 의 conditional edge 에서 이 세 route 모두 `single_shot`
노드로 내부 포워딩된다. Phase 5~7 에서 해당 노드가 추가되면 conditional edge
매핑 한 줄씩만 바꾸면 된다.

Phase 4-2 에서는 question_router 자체가 DB 기반 rule 을 먼저 조회하도록
확장될 예정 — 이 노드 쪽 로직은 그대로 두어도 된다.
"""

import logging

from chat.graph.routes import ROUTE_SINGLE_SHOT
from chat.graph.state import GraphState
from chat.services.question_router import route_question


logger = logging.getLogger(__name__)


def router_node(state: GraphState) -> dict:
    """state.question → RouteDecision → state.route/route_reason/matched_rules."""
    decision = route_question(state['question'])

    if decision.route != ROUTE_SINGLE_SHOT:
        # 아직 workflow/agent 노드가 없으니 실제 실행은 single_shot 으로 포워딩.
        # 의도된 경로는 state.route 에 정직하게 남아 관측 가능.
        logger.info(
            '라우팅: %s (reason=%s, rules=%s) — Phase 5~7 대기 중이라 single_shot 로 포워딩',
            decision.route, decision.reason, decision.matched_rules,
        )

    return {
        'route': decision.route,
        'route_reason': decision.reason,
        'matched_rules': list(decision.matched_rules),
    }

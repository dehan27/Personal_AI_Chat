"""질문 분류기 (Phase 4-2 / Phase 6-1 확장).

graph 의 router_node 가 부른다. 우선순위는:

    1. DB RouterRule 조회 (enabled=True, priority DESC) — 매치 있으면 해당 route
    2. 코드 상수(WORKFLOW_KEYWORDS / AGENT_KEYWORDS) 키워드 매칭
    3. 그래도 없으면 'single_shot' (default)

코드 상수는 **영구 보존되는 기본 동작**이다. DB rule 은 운영 중 조정하는
override 계층. BO 에서 rule 을 다 지우거나 DB 가 비어있어도 코드 키워드로
Phase 4-1 동작 그대로 유지된다.

workflow 가 agent 보다 먼저인 이유: 정형 계산은 저렴·안정적이므로 애매할 때
workflow 쪽이 안전. agent 는 탐색·비교가 명확할 때만 사용.

Phase 6-1 의 역할 정리:

- 코드 키워드(`WORKFLOW_KEYWORDS` / `AGENT_KEYWORDS`) 는 **route 선택만** 담당.
  어떤 generic workflow(예: `date_calculation`) 를 탈지를 결정하지 않는다.
  키워드 경로로 `route=workflow` 가 된 질문은 `workflow_key=''` 를 들고
  graph 로 내려가고, `workflow_node` 가 등록된 key 가 없음을 보고 single_shot
  으로 폴백한다 — 즉 Phase 4-1 동작과 동일.
- `workflow_key` 를 실제로 채우려면 BO `RouterRule` 에서 해당 rule 의 drop-down
  으로 domain 을 지정해야 한다 (예: `pattern='며칠'`, `route='workflow'`,
  `workflow_key='date_calculation'`).
- 이 키워드들이 '회사 규정(퇴직금·연차)' 같은 이름을 달고 있는 건 fallback 기본
  동작 정의를 위한 패턴 신호일 뿐, 특정 조직 도메인과 묶이지 않는다. 회사 전용
  계산식은 Phase 6 범위 밖이며 (설계 §4), 운영자가 BO rule 로 원하는 매핑을
  만들도록 유도하는 게 기본 방향.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from chat.graph.routes import ROUTE_AGENT, ROUTE_SINGLE_SHOT, ROUTE_WORKFLOW


# 정형 계산·산정 성격 질문 신호.
# BO RouterRule 이 비어있을 때의 fallback 키워드 — '기본 동작' 역할.
WORKFLOW_KEYWORDS: tuple[str, ...] = (
    '계산', '산정', '얼마', '몇 일', '몇 년', '평균', '합계', '차감',
    '근속', '퇴직금', '연차 계산', '잔여 연차', '급여', '수당',
    '입사일', '퇴사일', '최근 3개월',
)

# 비교·추천·상황판단 성격 질문 신호.
# agent 는 비용·불확실성이 커 마지막 수단 — 신호가 명확할 때만 쓰인다.
AGENT_KEYWORDS: tuple[str, ...] = (
    '비교', '추천', '유리', '불리', '종합', '예외', '케이스',
    '해석', '충돌', '만약', '내 상황', '어떤 게 나아',
)


@dataclass(frozen=True)
class RouteDecision:
    """라우터 결정.

    reason 포맷:
      - 'db_rule:<name>'      — DB RouterRule 매치
      - 'workflow_keyword'    — 코드 WORKFLOW_KEYWORDS 매치
      - 'agent_keyword'       — 코드 AGENT_KEYWORDS 매치
      - 'default'             — 아무것도 매치 안 됨
    """
    route: str
    reason: str
    matched_rules: List[str] = field(default_factory=list)


def _match_db_rules(question: str) -> Optional[RouteDecision]:
    """DB RouterRule 을 priority 순으로 순회해 첫 매치 반환. 없으면 None.

    lazy import 로 Django app 로딩 순서와 무관하게 동작.
    현재는 match_type='contains' 만 지원.
    """
    from chat.models import RouterRule  # lazy to avoid app-registry issues

    # Meta.ordering 이 (-priority, -updated_at) 이라 별도 order_by 불필요.
    for rule in RouterRule.objects.filter(enabled=True):
        if rule.match_type == RouterRule.MatchType.CONTAINS:
            if rule.pattern and rule.pattern in question:
                return RouteDecision(
                    route=rule.route,
                    reason=f'db_rule:{rule.name}',
                    matched_rules=[rule.pattern],
                )
        # 다른 match_type 은 아직 미지원 — 무시하고 다음 rule 로.
    return None


def _matches(question: str, keywords: tuple[str, ...]) -> List[str]:
    """question 안에 포함된 모든 키워드를 순서대로 반환. 없으면 빈 리스트."""
    return [kw for kw in keywords if kw in question]


def route_question(question: str) -> RouteDecision:
    """질문을 3 route 중 하나로 분류 (DB → 코드 fallback → default 순)."""
    db_decision = _match_db_rules(question)
    if db_decision is not None:
        return db_decision

    hits = _matches(question, WORKFLOW_KEYWORDS)
    if hits:
        return RouteDecision(
            route=ROUTE_WORKFLOW,
            reason='workflow_keyword',
            matched_rules=hits,
        )

    hits = _matches(question, AGENT_KEYWORDS)
    if hits:
        return RouteDecision(
            route=ROUTE_AGENT,
            reason='agent_keyword',
            matched_rules=hits,
        )

    return RouteDecision(route=ROUTE_SINGLE_SHOT, reason='default')

"""채팅 graph 의 route 값.

`GraphState.route` 필드 값이자 `add_conditional_edges` 매핑 키로 쓰인다.
문자열 리터럴이 코드 곳곳에 흩어지지 않도록 한 곳에 모아두고, question_router 의
분류 결과와 graph 의 분기 선택이 같은 상수를 참조하게 한다.

Phase 4-1 에서는 3 route 모두 `single_shot` 노드로 내부 포워딩 (workflow/agent 노드
미구현). Phase 4-2 에서 BO RouterRule 의 `route` choices 로도 이 튜플을 그대로 씀.
"""

ROUTE_SINGLE_SHOT = 'single_shot'
ROUTE_WORKFLOW = 'workflow'
ROUTE_AGENT = 'agent'

# 선언 순서 = BO choices 노출 순서가 될 가능성이 높으니 의미 있는 순서로 둔다.
ALL_ROUTES = (ROUTE_SINGLE_SHOT, ROUTE_WORKFLOW, ROUTE_AGENT)

"""Router node — 실행 경로 선택.

Phase 2 에서는 진짜 분기 로직 없이 항상 'single_shot' 만 반환하는 placeholder 다.
graph shape 를 Phase 4 이전에 미리 고정해 두기 위한 자리.

Phase 4 에서 확장 예정:
    - 규칙 기반 1차 판정 (키워드 / 질문 패턴 / 메타 힌트)
    - 필요 시 저비용 모델 분류 보조
    - 실패 시 상위 경로(single_shot → workflow → agent) 승급 정책
"""

from chat.graph.state import GraphState


def router_node(state: GraphState) -> dict:
    """Phase 2: 모든 요청을 single_shot 으로."""
    return {'route': 'single_shot'}

"""Single-shot node — 기존 query_pipeline.answer_question() 을 graph 안에서 재사용.

Phase 2 에서는 본체를 재작성하지 않는다. answer_question() 은 그대로 두고
여기선 호출 + 결과·에러를 state 로 옮기는 래퍼 역할만 한다.

Phase 3 에서 answer_question() 을 더 작은 함수들(검색 / 재정렬 / 프롬프트 조립 /
OpenAI 호출 / 저장)로 쪼개고 node 책임을 재정리할 예정. 그 시점까지 이 노드는
'호환성 보존' 목적이다.
"""

from chat.graph.state import GraphState
from chat.services.query_pipeline import QueryPipelineError, answer_question


def single_shot_node(state: GraphState) -> dict:
    """state.question + state.history → answer_question → state.result 또는 state.error."""
    try:
        result = answer_question(
            state['question'],
            history=state.get('history', []),
        )
    except QueryPipelineError as exc:
        # pipeline 에서 raise 한 오류만 문자열로 변환해서 state 에 싣는다.
        # 다른 예외는 의도치 않은 상황이므로 그대로 올려 Django 500 경로로 보낸다.
        return {'error': str(exc)}
    return {'result': result}

"""Single-shot node — single_shot 패키지의 파이프라인을 graph 에서 실행.

Phase 3 에서 `chat.services.single_shot.pipeline.run_single_shot` 으로 직접
연결. 예외는 node 안에서만 잡아 `state.error` 로 싣고, 다른 예외 타입은 그대로
올라가 Django 500 경로로 간다.
"""

from chat.graph.state import GraphState
from chat.services.single_shot.pipeline import run_single_shot
from chat.services.single_shot.types import QueryPipelineError


def single_shot_node(state: GraphState) -> dict:
    """state.question + state.history → run_single_shot → state.result 또는 state.error."""
    try:
        result = run_single_shot(
            state['question'],
            history=state.get('history', []),
        )
    except QueryPipelineError as exc:
        # pipeline 에서 raise 한 오류만 문자열로 변환해서 state 에 싣는다.
        # 다른 예외는 의도치 않은 상황이므로 그대로 올려 Django 500 경로로 보낸다.
        return {'error': str(exc)}
    return {'result': result}

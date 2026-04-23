"""컴파일된 LangGraph 와 외부 진입 함수.

view / service 는 오직 run_chat_graph(question, history) 만 쓴다. state 구조나
node 구성이 바뀌어도 이 함수의 시그니처·반환·예외는 고정이다 (Phase 3 이후에도).

현재 graph shape:
    START → router → (conditional on state.route) → single_shot → END
"""

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from chat.graph.nodes.router import router_node
from chat.graph.nodes.single_shot import single_shot_node
from chat.graph.state import GraphState
from chat.services.query_pipeline import QueryPipelineError, QueryResult


@lru_cache(maxsize=1)
def _compiled_graph():
    """프로세스당 한 번만 compile. runserver/gunicorn 프로세스 교체 시 자연 리셋."""
    builder = StateGraph(GraphState)
    builder.add_node('router', router_node)
    builder.add_node('single_shot', single_shot_node)

    builder.add_edge(START, 'router')
    builder.add_conditional_edges(
        'router',
        # state.route 값을 그대로 key 로 쓴다. Phase 4 에서 선택지가 늘어날 때
        # selector 는 건드리지 않고 아래 매핑에 키/노드만 추가하면 된다.
        lambda state: state['route'],
        {'single_shot': 'single_shot'},
    )
    builder.add_edge('single_shot', END)

    return builder.compile()


def run_chat_graph(question: str, history: list[dict]) -> QueryResult:
    """Phase 2 단일 외부 진입점.

    반환값은 QueryResult 그대로이며, 실패 시 QueryPipelineError 를 raise 한다.
    view 의 기존 try/except 블록을 그대로 사용할 수 있도록 시그니처를 맞췄다.
    """
    final = _compiled_graph().invoke({
        'question': question,
        'history': history,
    })

    # 노드 내부에서 포착된 에러 메시지가 실렸으면 그대로 전파.
    if final.get('error'):
        raise QueryPipelineError(final['error'])

    result = final.get('result')
    if result is None:
        # 정상 흐름에선 발생하지 않지만, 노드가 result 를 안 채운 상황을 조기에 드러냄.
        raise QueryPipelineError('graph 가 결과 없이 종료되었습니다.')
    return result

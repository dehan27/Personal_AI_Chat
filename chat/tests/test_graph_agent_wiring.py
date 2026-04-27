"""Phase 7-2 graph 결선 회귀 테스트.

목적: `chat/graph/app.py` 의 `_compiled_graph` 가 ROUTE_* 마다 올바른 노드로
분기하는지 — `add_node` 누락 / conditional edge 오타 / `add_edge` 누락을
단위 레벨에서 잡는다. 7-1 머지 시점까지 `chat.tests` 의 어떤 테스트도
`run_chat_graph` 를 호출하지 않았기에 본 PR 이 graph 단 자동 회귀 테스트의
첫 사례.

patch target 은 **import binding** 기준:

- `chat.graph.nodes.router.route_question` — `router_node` 가 모듈 상단에서
  `from chat.services.question_router import route_question` 으로 가져옴.
- `chat.graph.app.<node_name>` — `app.py` 가 노드 함수들을 import 해서 `add_node`
  에 박는다. 노드 모듈 쪽 (`chat.graph.nodes.X.X_node`) 을 patch 해도 graph 가
  들고 있는 binding 은 안 바뀐다.

`_compiled_graph` 는 `lru_cache(maxsize=1)` 라 patch 후 반드시 `cache_clear()`
를 호출해 patch 된 binding 으로 재컴파일해야 한다. 다음 테스트가 stub graph 를
재사용하지 않도록 `addCleanup` 로 다시 cache_clear.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from chat.graph.app import _compiled_graph, run_chat_graph
from chat.graph.routes import ROUTE_AGENT, ROUTE_SINGLE_SHOT, ROUTE_WORKFLOW
from chat.services.question_router import RouteDecision
from chat.services.single_shot.types import QueryResult


def _force_route(route_value):
    """router 가 지정 route 를 반환하도록 fake."""
    return RouteDecision(
        route=route_value,
        reason='test_forced',
        matched_rules=[],
        workflow_key='',
    )


def _stub_node(reply):
    """노드 함수 시그니처에 맞는 stub. state 받아 QueryResult 1건 싣는다."""
    def _node(state):
        return {
            'result': QueryResult(
                reply=reply,
                sources=[],
                total_tokens=0,
                chat_log_id=None,
            ),
        }
    return _node


class GraphAgentWiringTests(SimpleTestCase):
    def setUp(self):
        # 다른 테스트가 컴파일해 둔 graph 가 patch 영향을 못 받게 비운다.
        _compiled_graph.cache_clear()
        # 본 테스트가 끝나도 stub binding 이 lru_cache 에 남으면 곤란하므로 cleanup.
        self.addCleanup(_compiled_graph.cache_clear)

    def test_route_agent_dispatches_to_agent_node(self):
        with patch(
            'chat.graph.nodes.router.route_question',
            return_value=_force_route(ROUTE_AGENT),
        ), patch(
            'chat.graph.app.agent_node',
            side_effect=_stub_node('[agent stub]'),
        ) as agent_mock, patch(
            'chat.graph.app.single_shot_node',
            side_effect=_stub_node('[single_shot stub]'),
        ) as single_shot_mock, patch(
            'chat.graph.app.workflow_node',
            side_effect=_stub_node('[workflow stub]'),
        ) as workflow_mock:
            # patch 적용 직후 lru_cache 비워야 stub 으로 재컴파일된다.
            _compiled_graph.cache_clear()
            result = run_chat_graph('비교 질문', history=[])

        self.assertEqual(result.reply, '[agent stub]')
        self.assertEqual(agent_mock.call_count, 1)
        self.assertEqual(single_shot_mock.call_count, 0)
        self.assertEqual(workflow_mock.call_count, 0)

    def test_route_single_shot_dispatches_to_single_shot_node(self):
        with patch(
            'chat.graph.nodes.router.route_question',
            return_value=_force_route(ROUTE_SINGLE_SHOT),
        ), patch(
            'chat.graph.app.agent_node',
            side_effect=_stub_node('[agent stub]'),
        ) as agent_mock, patch(
            'chat.graph.app.single_shot_node',
            side_effect=_stub_node('[single_shot stub]'),
        ) as single_shot_mock, patch(
            'chat.graph.app.workflow_node',
            side_effect=_stub_node('[workflow stub]'),
        ) as workflow_mock:
            _compiled_graph.cache_clear()
            result = run_chat_graph('일반 질문', history=[])

        self.assertEqual(result.reply, '[single_shot stub]')
        self.assertEqual(single_shot_mock.call_count, 1)
        self.assertEqual(agent_mock.call_count, 0)
        self.assertEqual(workflow_mock.call_count, 0)

    def test_route_workflow_dispatches_to_workflow_node(self):
        with patch(
            'chat.graph.nodes.router.route_question',
            return_value=_force_route(ROUTE_WORKFLOW),
        ), patch(
            'chat.graph.app.agent_node',
            side_effect=_stub_node('[agent stub]'),
        ) as agent_mock, patch(
            'chat.graph.app.single_shot_node',
            side_effect=_stub_node('[single_shot stub]'),
        ) as single_shot_mock, patch(
            'chat.graph.app.workflow_node',
            side_effect=_stub_node('[workflow stub]'),
        ) as workflow_mock:
            _compiled_graph.cache_clear()
            result = run_chat_graph('계산 질문', history=[])

        self.assertEqual(result.reply, '[workflow stub]')
        self.assertEqual(workflow_mock.call_count, 1)
        self.assertEqual(agent_mock.call_count, 0)
        self.assertEqual(single_shot_mock.call_count, 0)

    def test_non_agent_route_does_not_invoke_agent_node(self):
        # ROUTE_SINGLE_SHOT / ROUTE_WORKFLOW 둘 다에서 agent_node 가 호출되지
        # 않는지 한 번에 확인 — conditional edge 매핑이 잘못 섞이는 회귀 가드.
        with patch(
            'chat.graph.nodes.router.route_question',
            side_effect=[
                _force_route(ROUTE_SINGLE_SHOT),
                _force_route(ROUTE_WORKFLOW),
            ],
        ), patch(
            'chat.graph.app.agent_node',
            side_effect=_stub_node('[agent stub]'),
        ) as agent_mock, patch(
            'chat.graph.app.single_shot_node',
            side_effect=_stub_node('[single_shot stub]'),
        ), patch(
            'chat.graph.app.workflow_node',
            side_effect=_stub_node('[workflow stub]'),
        ):
            _compiled_graph.cache_clear()
            run_chat_graph('q1', history=[])
            run_chat_graph('q2', history=[])

        self.assertEqual(agent_mock.call_count, 0)

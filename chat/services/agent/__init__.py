"""Generic agent runtime (Phase 7-1 runtime + Phase 7-2 graph 연결).

Phase 6 가 정형 절차형 질문(`single_shot` / `workflow`)을 다루는 두 줄기를 깔
았다면, Phase 7 의 agent 는 **여러 자료·도구를 반복적으로 호출**해야 답이 나오는
탐색형 질문을 위한 상위 오케스트레이터다. 회사 전용 업무 로직을 직접 구현하지
않고, 이미 있는 retrieval / canonical QA / workflow dispatch 를 **도구**처럼
재사용한다.

Phase 7 분할:

- **7-1** — runtime 본체 (`react / state / tools / tools_builtin / prompts /
  result`) + 단위 테스트. graph 와는 미연결 — `ROUTE_AGENT` 가 여전히 single_shot
  으로 폴백.
- **7-2 (현재)** — graph wiring 완료. `chat/graph/nodes/agent.py` 가 본 패키지의
  `run_agent` 와 `reply` 를 묶어 `ROUTE_AGENT` 진입점이 됐고, `chat/graph/app.py`
  의 conditional edge 가 single_shot 폴백에서 새 노드로 교체. agent reply
  포맷터(`reply.py`) 도 본 패키지에서 노출.

공개 API:

    from chat.services.agent.react import run_agent
    from chat.services.agent.reply import build_reply_from_agent_result
    result = run_agent(question, history)            # → AgentResult (Phase 8-1 부터)
    reply = build_reply_from_agent_result(result)    # → str (사용자-facing)
    sources = result.sources_as_dicts()              # → [{'name', 'url'}, ...]

Phase 8-1 변경: `run_agent` 반환형이 `WorkflowResult` → `AgentResult` 로 전환.
`AgentResult` 는 `BaseResult` Protocol implement + `termination` / `tool_calls` /
`sources` 1급 필드. workflow 와 호환되는 어댑터(`to_workflow_result()`) 도 제공.

설계 문서: `resources/plans/2.0.0_Phase 7_Agent_개발_설계.md`.
플랜 문서:
- `resources/plans/detail/2.0.0_Phase 7-1_agent_runtime_개발_플랜.md`
- `resources/plans/detail/2.0.0_Phase 7-2_agent_graph_wiring_개발_플랜.md`
"""

# tools_builtin 은 import 시점에 세 도구를 registry 에 등록하는 부작용을 갖는다
# (chat.workflows.domains.general 패턴과 동일). 이 패키지를 import 하는 순간
# `tools.all_entries()` 가 채워진다.
from chat.services.agent import tools_builtin  # noqa: F401

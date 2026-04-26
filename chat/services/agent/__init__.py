"""Generic agent runtime (Phase 7-1).

Phase 6 가 정형 절차형 질문(`single_shot` / `workflow`)을 다루는 두 줄기를 깔
았다면, Phase 7 의 agent 는 **여러 자료·도구를 반복적으로 호출**해야 답이 나오는
탐색형 질문을 위한 상위 오케스트레이터다. 회사 전용 업무 로직을 직접 구현하지
않고, 이미 있는 retrieval / canonical QA / workflow dispatch 를 **도구**처럼
재사용한다.

이 패키지는 **import 만으로 다른 모듈 동작에 영향 주지 않는다** — Phase 7-1 은
runtime 자체만 들이고 graph 와는 연결하지 않는다. `ROUTE_AGENT` 는 여전히
single_shot 으로 폴백된다 (Phase 4-1 동작 그대로). 실제 graph wiring · reply
포맷터 · BO smoke 는 Phase 7-2 의 책임.

공개 API:

    from chat.services.agent.react import run_agent
    result = run_agent(question, history)            # → WorkflowResult

설계 문서: `resources/plans/2.0.0_Phase 7_Agent_개발_설계.md`.
플랜 문서: `resources/plans/detail/2.0.0_Phase 7-1_agent_runtime_개발_플랜.md`.
"""

# tools_builtin 은 import 시점에 세 도구를 registry 에 등록하는 부작용을 갖는다
# (chat.workflows.domains.general 패턴과 동일). 이 패키지를 import 하는 순간
# `tools.all_entries()` 가 채워진다.
from chat.services.agent import tools_builtin  # noqa: F401

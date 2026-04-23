"""LangGraph state 스키마.

Phase 2 는 single-shot 한 경로만 의미있게 사용한다. 이후 Phase 4 에서 router 가
실제 분기를 시작하고, Phase 5~7 에서 workflow / agent 가 붙을 때도 같은 state
구조 위에 필드를 '추가' 만 한다는 원칙이다. 지금 존재하지 않는 필드를 미리
선언해 두지는 않는다 — 코드와 문서 양쪽에서 오해를 만들 수 있어서.

TypedDict 를 쓰는 이유:
    - 추가 의존성 0 (Pydantic 도입 회피)
    - LangGraph 가 공식 지원하는 가장 가벼운 state 방식
    - dataclass(QueryResult) 를 Optional 필드로 그대로 담을 수 있음
      (현재 checkpointer 를 쓰지 않으므로 직렬화 요구 없음)

total=False 로 둬서 호출자가 초기 상태를 구성할 때 result / error 같은
출력 필드를 생략할 수 있게 한다.
"""

from typing import Optional, TypedDict

from chat.services.query_pipeline import QueryResult


class GraphState(TypedDict, total=False):
    # ─── 입력 ──────────────────────────────────────────
    question: str            # 사용자 질문 원문
    history: list[dict]      # 세션 히스토리 [{'role':..., 'content':...}, ...]

    # ─── router 결과 ──────────────────────────────────
    route: str               # Phase 2 에서는 'single_shot' 만.
                             # Phase 4 에서 'workflow' / 'agent' 등 추가 예정.

    # ─── 실행 결과 ────────────────────────────────────
    result: Optional[QueryResult]

    # ─── 에러 전달 (노드 내부에서 포착한 메시지) ──────
    error: Optional[str]

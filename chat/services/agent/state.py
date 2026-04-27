"""ReAct loop 가 한 턴 동안 들고 다니는 상태 (Phase 7-1).

설계 §9 그대로:
    question / history / agent_goal / observations / tool_calls /
    iteration_count / final_answer / termination / error

원문 응답을 통째로 누적하면 다음 iteration 의 LLM 컨텍스트가 폭주하므로
`Observation` / `ToolCall` 은 **요약 형태** 로 저장한다.

Phase 7-1 결정: `question` 은 사용자 원본 입력 그대로 다룬다. history-aware
rewrite 의 적용 위치는 Phase 7-2 의 graph wiring 시 (a) graph node 가 결과를
같은 필드에 주입 / (b) `search_query` 같은 별도 필드 추가 중 하나로 결정한다.
7-1 은 (a) 기준이라 별도 필드는 미리 도입하지 않는다 (YAGNI).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional

from chat.services.agent.result import AgentTermination


# 한 Observation 의 요약 길이 상한. 너무 길면 다음 iteration 의 LLM 컨텍스트가
# 잠식되므로 자른다 — 끝에 '…' 를 붙여 잘림을 표시.
#
# Phase 7-1 은 600 으로 시작했으나 7-2 smoke 에서 retrieve_documents 의 청크 본문이
# 너무 강하게 잘려 LLM 이 데이터를 손에 쥐고도 답을 못 만드는 회귀가 발견됨.
# 1500 으로 늘려도 한 턴 최대 ≈ 6 step × 1500 = 9000자 — gpt-4o-mini 128k 한도에
# 여유. 근본적으로는 retrieve 결과를 workspace 에 두고 read_chunk 도구로 LLM 이
# 필요한 부분만 fetch 하는 구조가 옳지만 그건 후속 Phase 책임.
MAX_OBSERVATION_SUMMARY_CHARS = 1500


@dataclass(frozen=True)
class ToolCall:
    """LLM 이 한 step 에서 호출한 도구 + 인자 기록."""
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    """tool 호출 결과 요약. 원문이 아니라 LLM 이 다시 보기 좋은 짧은 문장이다."""
    tool: str
    summary: str
    is_failure: bool = False

    def __post_init__(self) -> None:
        # frozen dataclass 라 __setattr__ 우회. summary 길이는 직접 만든 객체에 한해
        # __post_init__ 에서 잘라 넣는다 (다른 곳에서 새로 만들 때 일관성 유지).
        if len(self.summary) > MAX_OBSERVATION_SUMMARY_CHARS:
            object.__setattr__(
                self,
                'summary',
                self.summary[:MAX_OBSERVATION_SUMMARY_CHARS - 1] + '…',
            )


@dataclass
class AgentState:
    """ReAct loop 의 mutable 상태.

    `dataclass(frozen=False)` — loop 가 매 iteration 에 필드를 갱신한다. 외부에
    노출되는 결과는 이 객체가 아니라 `to_workflow_result(...)` 로 변환된
    `WorkflowResult`.
    """

    question: str
    history: List[dict] = field(default_factory=list)
    agent_goal: str = ''
    observations: List[Observation] = field(default_factory=list)
    tool_calls: List[ToolCall] = field(default_factory=list)
    iteration_count: int = 0
    final_answer: Optional[str] = None
    termination: Optional[AgentTermination] = None
    error: Optional[str] = None

    def add_observation(
        self,
        tool: str,
        summary: str,
        *,
        is_failure: bool = False,
    ) -> Observation:
        """Observation 을 만들어 append + 반환. 호출부가 길이 자르기 신경 안 쓰게."""
        obs = Observation(tool=tool, summary=summary, is_failure=is_failure)
        self.observations.append(obs)
        return obs

    def record_tool_call(self, name: str, arguments: Mapping[str, Any]) -> ToolCall:
        """ToolCall 기록 + 반환. 같은 호출이 반복됐는지 추적할 때도 쓰임."""
        # frozen Mapping 으로 박아 두면 dict 변경에 의한 불변성 위반 방지.
        call = ToolCall(name=name, arguments=dict(arguments or {}))
        self.tool_calls.append(call)
        return call

    def consecutive_failures(self) -> int:
        """가장 최근부터 연속된 실패 Observation 수."""
        count = 0
        for obs in reversed(self.observations):
            if obs.is_failure:
                count += 1
            else:
                break
        return count

    def repeated_call_count(self, name: str, arguments: Mapping[str, Any]) -> int:
        """동일한 (name, arguments) 호출 횟수 — 무한 루프 가드 판단에 쓰인다."""
        target = (name, tuple(sorted(dict(arguments or {}).items())))
        count = 0
        for call in self.tool_calls:
            key = (call.name, tuple(sorted(dict(call.arguments).items())))
            if key == target:
                count += 1
        return count

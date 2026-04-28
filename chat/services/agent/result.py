"""Agent 결과 / 종료 사유 / WorkflowResult 어댑터 (Phase 7-1; Phase 8-1 AgentResult 분리).

Phase 7-1 은 `WorkflowResult` 를 재사용했지만, Phase 8-1 부터 agent 가 자신의
의미 (sources, tool_calls, termination) 를 1급 필드로 표현하기 위해 `AgentResult`
를 신규 도입. `WorkflowResult` 는 그대로 유지 (호출부 변경 0).

이 모듈에 정의된 것:

  - `AgentTermination` — ReAct loop 종료 사유 enum.
  - `SourceRef` — 답변 근거 출처 (single_shot / ChatLog 의 `{'name', 'url'}` dict
    호환).
  - `ToolCallTrace` — Observation 으로부터 1:1 파생되는 step trace.
  - `AgentResult` — agent 한 턴의 최종 결과 (BaseResult Protocol implement).
  - `to_agent_result(termination, *, value, reason, state)` — termination + state
    → AgentResult.
  - `to_workflow_result(termination, *, value, reason)` — Phase 7 호환 어댑터,
    내부에서 to_agent_result 를 호출 후 WorkflowResult 로 변환.

`UNSUPPORTED` 는 agent 자체가 만들지 않는다 — "이 workflow 는 본래 이 요청을 다루지
않음" 은 라우팅 결정의 책임 (설계 §5-3, §6-2).

순환 import 방지 (Phase 8-1):
- `state.py` 가 `SourceRef` 를 import (단방향 OK).
- `result.py` 의 `to_agent_result(state)` 의 state 인자는 런타임 duck-typed —
  `.observations` / `.iteration_count` 만 접근. AgentState 타입 어노테이션은
  `TYPE_CHECKING` 블록에서만 import 하고 시그니처는 forward-string `'AgentState'`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Tuple

from chat.workflows.core import WorkflowResult, WorkflowStatus

if TYPE_CHECKING:
    from chat.services.agent.state import AgentState  # noqa: F401


class AgentTermination(str, Enum):
    """ReAct loop 가 종료된 사유. 사용자에게 직접 노출되지는 않고, 내부 관측·로깅과
    `to_workflow_result` 의 분기 키로 쓰인다.
    """

    FINAL_ANSWER = 'final_answer'                  # LLM 이 정상적으로 답을 만들어 끝냄.
    MAX_ITERATIONS_EXCEEDED = 'max_iterations_exceeded'  # max_iterations 도달.
    NO_MORE_USEFUL_TOOLS = 'no_more_useful_tools'  # 같은 tool/argument 가 반복돼 진전 없음.
    INSUFFICIENT_EVIDENCE = 'insufficient_evidence'  # 도구를 다 돌았지만 근거 부족.
    FATAL_ERROR = 'fatal_error'                    # LLM 호출 / 파싱 / 예측 못 한 예외로 중단.


# 내부 종료 사유 → 사용자-facing WorkflowResult 변환 표.
# `OK / NOT_FOUND / UPSTREAM_ERROR` 만 사용. UNSUPPORTED 는 agent 가 만들지 않음.
#
# Phase 7-4 smoke 보강:
# - MAX_ITERATIONS_EXCEEDED → NOT_FOUND 매핑 (이전 UPSTREAM_ERROR 에서 변경).
#   "잠시 후 다시 시도" 가 의미상 부정확 — broad query 로 6 step 다 채운 케이스는
#   재시도해도 같은 결과. "정리 못 함" = NOT_FOUND 가 맞음. 카피도 "더 구체적인
#   질문" 안내로.
# - NO_MORE_USEFUL_TOOLS / INSUFFICIENT_EVIDENCE 카피를 "다시 물어봐" 톤으로 통일.
_DEFAULT_REASONS = {
    AgentTermination.MAX_ITERATIONS_EXCEEDED: (
        '충분한 답을 만들지 못했습니다. 더 구체적인 질문으로 다시 물어봐 주세요.'
    ),
    AgentTermination.NO_MORE_USEFUL_TOOLS: (
        '질문에 맞는 자료를 찾을 수 없었습니다. 질문을 다시 한 번 확인해 주세요.'
    ),
    AgentTermination.INSUFFICIENT_EVIDENCE: (
        '관련 자료를 충분히 확인하지 못했습니다. 질문을 다시 한 번 확인해 주세요.'
    ),
    AgentTermination.FATAL_ERROR: (
        '일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.'
    ),
}


def to_workflow_result(
    termination: AgentTermination,
    *,
    value: Any = None,
    reason: str = '',
) -> WorkflowResult:
    """`AgentTermination` 을 `WorkflowResult` 로 변환.

    Phase 7-4 매핑:
    - `FINAL_ANSWER` → `WorkflowResult.ok(value=<answer>, ...)`.
    - `MAX_ITERATIONS_EXCEEDED` → `WorkflowResult.not_found(...)`. (이전 UPSTREAM_ERROR
      에서 변경 — "잠시 후 재시도" 가 broad query 시나리오에 부정확.)
    - `FATAL_ERROR` → `WorkflowResult.upstream_error(...)`. LLM/네트워크 일시 오류
      만 진짜 재시도 권장.
    - `NO_MORE_USEFUL_TOOLS` / `INSUFFICIENT_EVIDENCE` → `WorkflowResult.not_found(...)`.

    `reason` 이 비어있으면 `_DEFAULT_REASONS` 의 한국어 카피가 사용된다.
    `FINAL_ANSWER` 는 `value` 가 필수.
    """
    if termination == AgentTermination.FINAL_ANSWER:
        if value is None:
            raise ValueError(
                'to_workflow_result: FINAL_ANSWER 는 value 가 필요합니다.'
            )
        return WorkflowResult.ok(
            value=value,
            details={'termination': termination.value},
        )

    effective_reason = reason or _DEFAULT_REASONS.get(termination, '')
    if termination == AgentTermination.FATAL_ERROR:
        # 진짜 일시적 오류만 UPSTREAM_ERROR ("잠시 후 다시 시도" 적합).
        return WorkflowResult.upstream_error(effective_reason)

    if termination in (
        AgentTermination.MAX_ITERATIONS_EXCEEDED,
        AgentTermination.NO_MORE_USEFUL_TOOLS,
        AgentTermination.INSUFFICIENT_EVIDENCE,
    ):
        # max_iter 도달도 "정리 못 함" = NOT_FOUND. 재시도해도 같은 결과.
        return WorkflowResult.not_found(effective_reason)

    raise ValueError(f'to_workflow_result: 알 수 없는 termination: {termination!r}')


# ---------------------------------------------------------------------------
# Phase 8-1: AgentResult 및 부품 (SourceRef / ToolCallTrace)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceRef:
    """답변 근거 출처. single_shot / `ChatLog.sources` 의 dict 형식과 호환.

    `to_dict()` 가 `{'name': ..., 'url': ...}` 를 돌려주므로 `QueryResult.sources`
    에 그대로 박을 수 있다 — UI 변경 없이 출처 패널에 노출.
    """

    name: str
    url: str

    def to_dict(self) -> Dict[str, str]:
        return {'name': self.name, 'url': self.url}


@dataclass(frozen=True)
class ToolCallTrace:
    """ReAct loop 의 한 step trace. `Observation` 에서 1:1 파생.

    `tool='_llm'` 같은 LLM 자체 step (parse 실패 등) 도 포함 — 운영자가 디버깅
    시 모든 step 을 본다. `state.tool_calls` 가 아니라 `state.observations` 만
    walk 해 만들어진다 (P2-1 의 1:1 매칭 보장 정책).
    """

    tool: str
    arguments: Mapping[str, Any]
    is_failure: bool
    failure_kind: Optional[str]
    summary: str


@dataclass(frozen=True)
class AgentResult:
    """agent 한 턴의 최종 결과 (Phase 8-1).

    `BaseResult` Protocol implement — `status / value / details` 공유. 추가 필드:
    - `termination` — 1급 필드로 승격 (이전엔 `details['termination']`).
    - `tool_calls` — Observation 으로부터 파생된 step trace 튜플.
    - `sources` — retrieve 의 의미 매치 hit 의 SourceRef dedup 튜플.

    sources 정책 (Phase 8-1 plan §3):
    - 수집: `_retrieve_callable` 이 `hits[0]` 한 건만 `Observation.evidence` 에 넣음.
    - 부착: `tools.call` 이 result dict 의 `'evidence'` 키를 obs.evidence 로.
    - 모음: `to_agent_result(state)` 가 `state.observations` 의 evidence 를 dedup.
    - low_relevance failure 의 evidence 는 제외.
    - status 무관 노출 — agent_node 가 status 분기 없이 sources_as_dicts() 호출.
    """

    status: WorkflowStatus
    value: Any = None
    details: Mapping[str, Any] = field(default_factory=dict)
    termination: Optional[AgentTermination] = None
    tool_calls: Tuple[ToolCallTrace, ...] = ()
    sources: Tuple[SourceRef, ...] = ()

    def sources_as_dicts(self) -> List[Dict[str, str]]:
        """`QueryResult.sources` 형식 (`[{'name', 'url'}, ...]`) 으로 변환."""
        return [s.to_dict() for s in self.sources]

    def to_workflow_result(self) -> WorkflowResult:
        """Phase 7 호환 어댑터 — 외부 도구가 dict 로 보고 싶을 때 쓸 수 있게.

        details 에 termination / tool_calls / sources 를 그대로 박는다 (사용 안 해도
        됨). agent_node 와 reply layer 는 AgentResult 를 직접 받지만, 향후 통합
        대시보드 / log 분석 등에서 dict 한 형태로 정렬하고 싶을 때 사용.
        """
        from types import MappingProxyType

        details = dict(self.details)
        if self.termination is not None:
            details.setdefault('termination', self.termination.value)
        details.setdefault(
            'tool_calls',
            [
                {
                    'tool': t.tool, 'arguments': dict(t.arguments),
                    'is_failure': t.is_failure, 'failure_kind': t.failure_kind,
                }
                for t in self.tool_calls
            ],
        )
        details.setdefault('sources', self.sources_as_dicts())
        if self.status == WorkflowStatus.OK:
            return WorkflowResult.ok(value=self.value, details=details)
        # NOT_FOUND / UPSTREAM_ERROR / 그 외 — details 기반 어댑터.
        reason = self.details.get('reason', '') if self.details else ''
        if self.status == WorkflowStatus.UPSTREAM_ERROR:
            return WorkflowResult.upstream_error(reason)
        return WorkflowResult.not_found(reason)


def to_agent_result(
    termination: AgentTermination,
    *,
    value: Any = None,
    reason: str = '',
    state: Optional['AgentState'] = None,
) -> AgentResult:
    """`AgentTermination` + (optional) state → `AgentResult` 변환.

    state 가 있으면 `state.observations` 를 walk 해 `tool_calls` (모든 step) +
    `sources` (low_relevance 제외 evidence dedup) 를 채운다. state=None 이면
    빈 trace / 빈 sources.

    매핑은 `to_workflow_result` 와 동일:
    - FINAL_ANSWER → OK + value
    - FATAL_ERROR → UPSTREAM_ERROR + reason
    - 그 외 → NOT_FOUND + reason
    """
    if termination == AgentTermination.FINAL_ANSWER:
        if value is None:
            raise ValueError(
                'to_agent_result: FINAL_ANSWER 는 value 가 필요합니다.'
            )
        status = WorkflowStatus.OK
        details: Mapping[str, Any] = {'termination': termination.value}
    else:
        effective_reason = reason or _DEFAULT_REASONS.get(termination, '')
        details = {'termination': termination.value, 'reason': effective_reason}
        if termination == AgentTermination.FATAL_ERROR:
            status = WorkflowStatus.UPSTREAM_ERROR
        else:
            status = WorkflowStatus.NOT_FOUND

    tool_calls: Tuple[ToolCallTrace, ...] = ()
    sources: Tuple[SourceRef, ...] = ()
    if state is not None:
        tool_calls = tuple(
            ToolCallTrace(
                tool=obs.tool,
                arguments=dict(obs.arguments),
                is_failure=obs.is_failure,
                failure_kind=obs.failure_kind,
                summary=obs.summary,
            )
            for obs in state.observations
        )
        # sources: low_relevance evidence 는 제외, dedup (name, url) 키.
        seen: set = set()
        accumulated: List[SourceRef] = []
        for obs in state.observations:
            if obs.is_failure and obs.failure_kind == 'low_relevance':
                continue
            for ref in obs.evidence:
                key = (ref.name, ref.url)
                if key in seen:
                    continue
                seen.add(key)
                accumulated.append(ref)
        sources = tuple(accumulated)

    return AgentResult(
        status=status,
        value=value if status == WorkflowStatus.OK else None,
        details=details,
        termination=termination,
        tool_calls=tool_calls,
        sources=sources,
    )

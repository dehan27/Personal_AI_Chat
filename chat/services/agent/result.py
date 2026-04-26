"""Agent 종료 사유 → user-facing `WorkflowResult` 어댑터 (Phase 7-1).

Phase 7-1 결정: **agent runtime 은 새 결과 dataclass 를 만들지 않는다**. Phase 5
`WorkflowResult` 를 그대로 반환형으로 사용한다 (이름은 약간 어색하지만 reply 분기를
이미 Phase 6-3 가 status 기준으로 통일해 놨기 때문에 변경 비용 최소). 이 모듈은:

  - `AgentTermination` enum: ReAct loop 가 한 step 을 끝낼 때 결정한 내부 사유.
  - `to_workflow_result(termination, *, value=None, reason='')`: termination → `WorkflowResult` 변환.

`UNSUPPORTED` 는 agent 자체가 만들지 않는다 — "이 workflow 는 본래 이 요청을 다루지
않음" 은 라우팅 결정의 책임 (설계 §5-3, §6-2). agent 가 시작했는데 unsupported 로
끝나면 그건 라우터의 잘못이라고 본다.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from chat.workflows.core import WorkflowResult


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
_DEFAULT_REASONS = {
    AgentTermination.MAX_ITERATIONS_EXCEEDED: (
        '도구를 너무 많이 사용했어요. 잠시 후 다시 시도해 주세요.'
    ),
    AgentTermination.NO_MORE_USEFUL_TOOLS: (
        '더 알아볼 도구가 남지 않아 충분한 답을 만들 수 없었습니다.'
    ),
    AgentTermination.INSUFFICIENT_EVIDENCE: (
        '근거가 부족해 답을 만들기 어려웠습니다. 자료가 충분한지 확인해 주세요.'
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

    - `FINAL_ANSWER` → `WorkflowResult.ok(value=<answer>, ...)`.
    - `MAX_ITERATIONS_EXCEEDED` / `FATAL_ERROR` → `WorkflowResult.upstream_error(...)`.
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
    if termination in (
        AgentTermination.MAX_ITERATIONS_EXCEEDED,
        AgentTermination.FATAL_ERROR,
    ):
        return WorkflowResult.upstream_error(effective_reason)

    if termination in (
        AgentTermination.NO_MORE_USEFUL_TOOLS,
        AgentTermination.INSUFFICIENT_EVIDENCE,
    ):
        return WorkflowResult.not_found(effective_reason)

    raise ValueError(f'to_workflow_result: 알 수 없는 termination: {termination!r}')

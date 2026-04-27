"""Agent `WorkflowResult` → 사용자 reply 문자열 (Phase 7-2).

Phase 7-1 의 `to_workflow_result` 가 ReAct 종료 사유를 세 status 중 하나로
축약했으므로, 본 모듈은 그 셋만 다룬다:

- `OK`             — `result.value` 가 LLM 이 작성한 한국어 final_answer. 그대로 노출.
- `NOT_FOUND`      — `details['reason']` 한국어 카피 pass-through (없으면 기본 카피).
- `UPSTREAM_ERROR` — 동일 패턴.

`MISSING_INPUT / INVALID_INPUT / UNSUPPORTED` 는 agent runtime 이 만들 수 없는
status 다 (`chat/services/agent/result.py` 의 매핑 표 참고). 도달하면 runtime 의
status 어휘 regression 이라 보고 `ValueError` 로 fail-fast — 친절히 잡지 않는 것이
의도. (Plan §5-1 의 invariant guard 정책.)

`chat/workflows/domains/reply.py` 와 합치지 않은 이유: workflow reply 는
`workflow_key` 별 OK 포맷터 분기가 본질이지만, agent 는 key 가 없고 status 도
세 종류로 좁다. 한 모듈에 끼우면 분기 hack 이 늘어 가독성이 깨진다.
"""

from __future__ import annotations

from chat.workflows.core import WorkflowResult, WorkflowStatus


# Phase 7-1 result.py 의 _DEFAULT_REASONS 가 이미 한국어 카피를 details['reason']
# 에 박아주지만, 안전망 차원에서 reply 단에도 동일한 fallback 카피를 둔다 — agent
# 가 직접 details 를 비워서 not_found / upstream_error 를 만든 경우(외부 호출자
# 우회 경로)에도 깨지지 않게.
_DEFAULT_NOT_FOUND_REPLY = (
    '요청에 맞는 자료를 찾지 못했습니다. 관련 문서가 업로드되어 있는지 확인해 주세요.'
)
_DEFAULT_UPSTREAM_ERROR_REPLY = (
    '일시적인 오류로 이번 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.'
)


def build_reply_from_agent_result(result: WorkflowResult) -> str:
    """`WorkflowResult` 를 사용자 응답 문자열로 변환.

    agent 가 만들 수 있는 세 status 만 처리한다. 그 외 status 는 runtime 의
    invariant 위반이므로 ValueError 로 가시화한다 — Plan §5-1.
    """
    status = result.status

    if status == WorkflowStatus.OK:
        # final_answer 는 이미 한국어 응답으로 작성된 자연어. 그대로 보여준다.
        return str(result.value if result.value is not None else '')

    reason = (result.details or {}).get('reason') or ''

    if status == WorkflowStatus.NOT_FOUND:
        return reason or _DEFAULT_NOT_FOUND_REPLY

    if status == WorkflowStatus.UPSTREAM_ERROR:
        return reason or _DEFAULT_UPSTREAM_ERROR_REPLY

    # 도달 불가 status — runtime regression 시그널.
    raise ValueError(
        f'agent 가 만들면 안 되는 status 입니다: {status!r}. '
        '`chat/services/agent/result.py` 의 to_workflow_result 매핑 표를 확인하세요.'
    )

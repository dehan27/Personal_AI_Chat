"""WorkflowResult → 사용자에게 보여줄 한국어 reply 문자열 (Phase 6-1).

graph `workflow_node` 가 domain workflow 로부터 받은 `WorkflowResult` 를
이 모듈에 넘기면 상태별 자연어 답변을 돌려준다. LLM 호출 없이 결정적 포맷팅만.

상태별 규칙:
- `OK`              — 등록된 key 별 포맷터(`_ok_formatters`)가 있으면 그걸 쓰고,
                      없으면 범용 fallback("`value` 결과: ...").
- `MISSING_INPUT`   — "계산하려면 {필드 목록} 정보가 필요합니다."
- `INVALID_INPUT`   — errors 를 줄바꿈으로 연결해 노출. 없으면 일반 안내.
- `UNSUPPORTED`     — details.reason 우선, 없으면 기본 문구.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from chat.workflows.core import WorkflowResult, WorkflowStatus


def build_reply_from_result(
    result: WorkflowResult,
    *,
    workflow_key: str,
) -> str:
    """`WorkflowResult` 하나를 사용자 응답 문자열로 변환."""
    status = result.status

    if status == WorkflowStatus.OK:
        formatter = _ok_formatters.get(workflow_key, _ok_default)
        return formatter(result)

    if status == WorkflowStatus.MISSING_INPUT:
        fields = _comma_join(result.missing_fields)
        if fields:
            return f'계산하려면 {fields} 정보가 필요합니다.'
        return '필요한 정보가 부족해 계산할 수 없습니다.'

    if status == WorkflowStatus.INVALID_INPUT:
        errors = result.details.get('errors') if result.details else None
        if errors:
            lines = '\n'.join(f'- {e}' for e in errors)
            return f'입력이 올바르지 않습니다.\n{lines}'
        return '입력이 올바르지 않습니다.'

    # UNSUPPORTED
    reason = result.details.get('reason') if result.details else None
    if reason:
        return f'현재 지원하지 않는 작업입니다. ({reason})'
    return '현재 지원하지 않는 작업입니다.'


# ---------------------------------------------------------------------------
# key 별 OK 포맷터
# ---------------------------------------------------------------------------

def _ok_date_calculation(result: WorkflowResult) -> str:
    details = result.details or {}
    start = details.get('start') or ''
    end = details.get('end') or ''
    unit_label = details.get('unit_label') or ''
    return f'{start} 부터 {end} 까지 {result.value}{unit_label} 입니다.'


# op 별 라벨 + 맞는 한국어 조사(은/는). 받침 유무가 달라 일괄 '은' 을 쓰면
# "합계은"/"차이은" 처럼 어색해지기 때문에 op 마다 고정한다.
_AMOUNT_OP_LABELS: Mapping[str, tuple[str, str]] = {
    'sum':     ('합계', '는'),
    'average': ('평균', '은'),
    'diff':    ('차이', '는'),
}


def _ok_amount_calculation(result: WorkflowResult) -> str:
    details = result.details or {}
    op = details.get('op', '')
    label, particle = _AMOUNT_OP_LABELS.get(op, ('결과', '는'))
    value = result.value
    # 정수는 천단위 콤마, 실수는 소수 둘째 자리까지 정리 — 평균 결과의 가독성.
    if isinstance(value, int):
        value_str = f'{value:,}'
    elif isinstance(value, float):
        value_str = f'{value:,.2f}'
    else:
        value_str = str(value)
    return f'{label}{particle} {value_str} 입니다.'


def _ok_default(result: WorkflowResult) -> str:
    return f'결과: {result.value}'


_ok_formatters: Mapping[str, Callable[[WorkflowResult], str]] = {
    'date_calculation': _ok_date_calculation,
    'amount_calculation': _ok_amount_calculation,
}


# ---------------------------------------------------------------------------
# 내부 유틸
# ---------------------------------------------------------------------------

def _comma_join(items) -> str:
    """('a', 'b') → 'a, b'; 빈 이터러블은 빈 문자열."""
    return ', '.join(str(x) for x in items if x)

"""`amount_calculation` — 금액/숫자 목록에 sum / average / diff 를 적용 (Phase 6-2).

입력 (`Mapping[str, Any]`):
    values: list[int | str]  — 정규화 전에도 허용 (Phase 5 `parse_int_like` 가 정리).
    op:     str              — 'sum' | 'average' | 'diff' (기본 'sum').

처리:
    1) require_fields({'values'}) → 부족 시 MISSING_INPUT.
    2) values 가 list 가 아니거나 정수 변환 불가면 INVALID_INPUT.
    3) op 가 diff 인데 values 가 2 개 미만이면 INVALID_INPUT.
    4) op 에 따라 sum_amounts / average_amount / max-min 계산.
    5) WorkflowResult.ok(value=..., details={op, values, ...}).

LLM 호출 없음. Phase 5 core 만 조합한다.
"""

from __future__ import annotations

from typing import Any, Mapping

from chat.workflows.core import (
    ValidationResult,
    WorkflowResult,
    average_amount,
    combine_validations,
    parse_int_like,
    require_fields,
    sum_amounts,
)
from chat.workflows.domains import registry
from chat.workflows.domains.field_spec import FieldSpec


WORKFLOW_KEY = 'amount_calculation'

_SUPPORTED_OPS: tuple[str, ...] = ('sum', 'average', 'diff')

INPUT_SCHEMA = {
    'values': FieldSpec(
        type='number_list',
        required=True,
        aliases=('values', '금액', '숫자'),
    ),
    'op': FieldSpec(
        type='enum',
        required=False,
        default='sum',
        aliases=('op', '연산', '방식'),
        enum_values={
            'sum':     ('합계', '합', 'total', '전체', '더해'),
            'average': ('평균', 'average', 'avg'),
            'diff':    ('차이', 'difference', '차'),
        },
    ),
}


class AmountCalculationWorkflow:
    """Phase 5 `BaseWorkflow` 프로토콜 구현."""

    def prepare(self, raw: Mapping[str, Any]) -> Mapping[str, Any]:
        op = raw.get('op') or 'sum'
        return {
            'values': raw.get('values'),
            'op': op,
        }

    def validate(self, normalized: Mapping[str, Any]) -> ValidationResult:
        errors: list[str] = []

        req = require_fields(normalized, ['values'])

        op = normalized.get('op') or 'sum'
        if op not in _SUPPORTED_OPS:
            errors.append(
                f'op 은 {", ".join(_SUPPORTED_OPS)} 중 하나여야 합니다 '
                f'(받은 값: {op!r}).'
            )

        values = normalized.get('values')
        if values is not None:
            if not isinstance(values, (list, tuple)):
                errors.append('values 는 숫자 목록이어야 합니다.')
            else:
                # 전부 parse 가능해야 한다.
                for v in values:
                    try:
                        parse_int_like(v)
                    except (TypeError, ValueError):
                        errors.append(f'숫자로 해석할 수 없는 값이 있습니다: {v!r}')
                        break
                if op == 'diff' and isinstance(values, (list, tuple)) and len(values) < 2:
                    errors.append('diff 연산은 값이 2개 이상 필요합니다.')

        extra = ValidationResult.fail(errors=errors) if errors else ValidationResult.success()
        return combine_validations(req, extra)

    def execute(self, normalized: Mapping[str, Any]) -> WorkflowResult:
        raw_values = normalized['values']
        values = [parse_int_like(v) for v in raw_values]
        op = normalized.get('op') or 'sum'

        if op == 'sum':
            value = sum_amounts(values)
        elif op == 'average':
            value = average_amount(values)
        elif op == 'diff':
            value = max(values) - min(values)
        else:  # 안전망 — validate 에서 이미 걸러졌음.
            return WorkflowResult.invalid_input(['알 수 없는 op'])

        return WorkflowResult.ok(
            value=value,
            details={
                'op': op,
                'values': values,
                'count': len(values),
            },
        )


registry.register(
    registry.WorkflowEntry(
        key=WORKFLOW_KEY,
        title='금액 계산',
        description='숫자/금액 목록의 합계·평균·차이를 계산합니다.',
        status=registry.STATUS_STABLE,
        factory=lambda: AmountCalculationWorkflow(),
        input_schema=INPUT_SCHEMA,
    )
)

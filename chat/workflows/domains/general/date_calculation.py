"""`date_calculation` — 두 날짜 사이 기간을 계산하는 generic workflow (Phase 6-1).

입력 (`Mapping[str, Any]`):
    start: str  — 시작 날짜 (`core.parse_date` 가 지원하는 포맷 중 하나)
    end:   str  — 종료 날짜 (동일)
    unit:  str  — 'days' | 'months' | 'years' (기본 'days')

처리:
    1) require_fields({'start', 'end'})          → 부족 시 MISSING_INPUT
    2) parse_date(start) / parse_date(end)       → 실패 시 INVALID_INPUT
    3) ensure_date_order(start, end)             → 실패 시 INVALID_INPUT
    4) unit 에 맞춰 days/months/years_between 호출
    5) WorkflowResult.ok(value=<int>, details={'start','end','unit'})

LLM 호출 없음. Phase 5 core 만 조합. 회사 도메인 규정과 무관한 "두 날짜 사이
기간" 자체만 다룬다.
"""

from __future__ import annotations

from typing import Any, Mapping

from chat.workflows.core import (
    ValidationResult,
    WorkflowResult,
    combine_validations,
    days_between,
    ensure_date_order,
    months_between,
    parse_date,
    require_fields,
    years_between,
)
from chat.workflows.domains import registry
from chat.workflows.domains.field_spec import FieldSpec


WORKFLOW_KEY = 'date_calculation'

INPUT_SCHEMA = {
    'start': FieldSpec(
        type='date',
        required=True,
        aliases=('start', '시작', '시작일', '부터'),
    ),
    'end': FieldSpec(
        type='date',
        required=True,
        aliases=('end', '종료', '종료일', '끝', '까지'),
    ),
    'unit': FieldSpec(
        type='enum',
        required=False,
        default='days',
        aliases=('unit', '단위'),
        enum_values={
            'days':   ('일', '며칠', 'days'),
            'months': ('개월', '달', 'months'),
            'years':  ('년', 'years'),
        },
    ),
}

# 지원하는 기간 단위 — 한국어 표시용 라벨을 함께 묶어 reply 포맷에서 쓴다.
_SUPPORTED_UNITS: dict[str, str] = {
    'days': '일',
    'months': '개월',
    'years': '년',
}
_DEFAULT_UNIT = 'days'


class DateCalculationWorkflow:
    """Phase 5 `BaseWorkflow` 프로토콜 구현 (prepare / validate / execute)."""

    def prepare(self, raw: Mapping[str, Any]) -> Mapping[str, Any]:
        # 정규화 단계는 문자열 trim + unit 디폴트 처리까지만. 실제 파싱은 execute 에서.
        def _clean(value):
            if isinstance(value, str):
                return value.strip() or None
            return value

        return {
            'start': _clean(raw.get('start')),
            'end': _clean(raw.get('end')),
            'unit': (raw.get('unit') or _DEFAULT_UNIT),
        }

    def validate(self, normalized: Mapping[str, Any]) -> ValidationResult:
        errors: list[str] = []

        # 1) 필수값 — require_fields 가 결과를 돌려준다.
        req = require_fields(normalized, ['start', 'end'])

        # 2) unit 검증은 값 존재 여부와 독립적으로 미리 해둔다 — 미지원 unit 은
        #    도메인 규칙 위반이라 INVALID_INPUT 에 담고, missing 과 함께 전달한다.
        unit = normalized.get('unit') or _DEFAULT_UNIT
        if unit not in _SUPPORTED_UNITS:
            errors.append(
                f'unit 은 {", ".join(_SUPPORTED_UNITS)} 중 하나여야 합니다 '
                f'(받은 값: {unit!r}).'
            )

        # 3) 날짜 형식 + 순서는 start/end 가 모두 존재할 때만 의미가 있다.
        if req.ok and normalized.get('start') and normalized.get('end'):
            order = ensure_date_order(normalized['start'], normalized['end'])
            if not order.ok:
                errors.extend(order.errors)

        unit_fail = (
            ValidationResult.fail(errors=errors) if errors else ValidationResult.success()
        )
        return combine_validations(req, unit_fail)

    def execute(self, normalized: Mapping[str, Any]) -> WorkflowResult:
        start = parse_date(normalized['start'])
        end = parse_date(normalized['end'])
        unit = normalized.get('unit') or _DEFAULT_UNIT

        if unit == 'months':
            value = months_between(start, end)
        elif unit == 'years':
            value = years_between(start, end)
        else:
            value = days_between(start, end)

        return WorkflowResult.ok(
            value=value,
            details={
                'start': start.isoformat(),
                'end': end.isoformat(),
                'unit': unit,
                'unit_label': _SUPPORTED_UNITS.get(unit, unit),
            },
        )


# ---------------------------------------------------------------------------
# registry 등록 — 이 모듈이 import 되는 시점에 부작용으로 수행.
# 테스트에서 registry._reset_for_tests() 를 부른 뒤 다시 `import` 로 재등록하려면
# importlib.reload 가 필요하므로, 그런 시나리오는 dispatch/registry 단위 테스트가
# 자체 엔트리를 등록해 해결한다.
# ---------------------------------------------------------------------------

registry.register(
    registry.WorkflowEntry(
        key=WORKFLOW_KEY,
        title='날짜 계산',
        description='두 날짜 사이 기간(일/개월/년) 을 계산합니다.',
        status=registry.STATUS_STABLE,
        factory=lambda: DateCalculationWorkflow(),
        input_schema=INPUT_SCHEMA,
    )
)

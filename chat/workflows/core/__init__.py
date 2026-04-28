"""Workflow core — 도메인 무관 공통 계산·정규화·검증·포맷팅 레이어 (Phase 5).

Phase 6 도메인 workflow 는 이 패키지의 공개 API 만 import 해서 쓰면 된다.
내부 모듈 직접 import(예: `chat.workflows.core.dates.parse_date`)는 허용되지만
여기 재노출된 심볼을 쓰는 게 안정적이다.

의존 방향(엄격):
    result ← validation / dates / numbers / formatting ← base
"""

from chat.workflows.core.base import (
    BaseWorkflow,
    run_workflow,
)
from chat.workflows.core.dates import (
    days_between,
    ensure_date_order,
    months_between,
    parse_date,
    years_between,
)
from chat.workflows.core.formatting import (
    format_currency,
    format_date,
    format_duration,
)
from chat.workflows.core.numbers import (
    average_amount,
    parse_int_like,
    parse_money,
    sum_amounts,
)
from chat.workflows.core.result import (
    BaseResult,
    ValidationResult,
    WorkflowResult,
    WorkflowStatus,
)
from chat.workflows.core.tables import (
    parse_markdown_tables,
    serialize_table,
)
from chat.workflows.core.validation import (
    combine_validations,
    require_fields,
    require_non_empty,
)


__all__ = [
    # 결과 타입
    'BaseResult',
    'ValidationResult',
    'WorkflowResult',
    'WorkflowStatus',
    # 검증
    'combine_validations',
    'require_fields',
    'require_non_empty',
    # 날짜
    'days_between',
    'ensure_date_order',
    'months_between',
    'parse_date',
    'years_between',
    # 숫자
    'average_amount',
    'parse_int_like',
    'parse_money',
    'sum_amounts',
    # 포맷
    'format_currency',
    'format_date',
    'format_duration',
    # 표 파서 (Phase 6-3)
    'parse_markdown_tables',
    'serialize_table',
    # 실행 계약
    'BaseWorkflow',
    'run_workflow',
]

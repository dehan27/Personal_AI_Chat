"""`table_lookup` — 업로드된 문서의 표에서 셀 값을 찾는 generic workflow (Phase 6-3).

입력 (`Mapping[str, Any]`):
    query: str   — 사용자가 찾는 값을 묘사한 자유형 질문
                   (`chat/workflows/domains/field_spec.py` 의 'text' 타입으로 선언).

이 커밋(스켈레톤)은 구조와 input_schema / registry 연결에 집중한다. retrieval
+ LLM 결합 로직은 다음 커밋에서 `execute` 를 덮어쓴다. 그때까지는 query 가
있는지 확인 후 `WorkflowResult.ok(value=query)` 만 돌려준다 — 임시 응답.

상태 분리 정책(다음 커밋 반영):
    - 후보 표 0건 → NOT_FOUND
    - LLM 빈 응답 → NOT_FOUND
    - LLM 예외 / JSON 파싱 실패 → UPSTREAM_ERROR
    - UNSUPPORTED 는 이 workflow 에서 사용하지 않는다 (미등록 key 는 dispatch
      단에서 이미 처리됨).
"""

from __future__ import annotations

from typing import Any, Mapping

from chat.workflows.core import (
    ValidationResult,
    WorkflowResult,
    require_fields,
)
from chat.workflows.domains import registry
from chat.workflows.domains.field_spec import FieldSpec


WORKFLOW_KEY = 'table_lookup'

INPUT_SCHEMA = {
    'query': FieldSpec(
        type='text',
        required=True,
        aliases=('query', '질문', '찾을 항목'),
    ),
}


class TableLookupWorkflow:
    """Phase 5 `BaseWorkflow` 프로토콜 구현 (Phase 6-3 스켈레톤)."""

    def prepare(self, raw: Mapping[str, Any]) -> Mapping[str, Any]:
        query = raw.get('query')
        if isinstance(query, str):
            query = query.strip() or None
        return {'query': query}

    def validate(self, normalized: Mapping[str, Any]) -> ValidationResult:
        return require_fields(normalized, ['query'])

    def execute(self, normalized: Mapping[str, Any]) -> WorkflowResult:
        # 스켈레톤: retrieval / LLM 연결은 다음 커밋에서. 여기서는 query 그대로
        # 돌려주어 workflow 경로 자체가 살아있음을 확인한다.
        return WorkflowResult.ok(
            value=normalized['query'],
            details={'placeholder': True},
        )


registry.register(
    registry.WorkflowEntry(
        key=WORKFLOW_KEY,
        title='표 조회',
        description='업로드된 문서의 표에서 사용자가 묻는 셀 값을 찾아 반환합니다.',
        status=registry.STATUS_BETA,  # 스켈레톤 단계 — 다음 커밋에서 stable 로 승격
        factory=lambda: TableLookupWorkflow(),
        input_schema=INPUT_SCHEMA,
    )
)

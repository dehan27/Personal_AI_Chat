"""`table_lookup` — 업로드된 문서의 표에서 셀 값을 찾는 generic workflow (Phase 6-3).

입력 (`Mapping[str, Any]`):
    query: str   — 사용자가 찾는 값을 묘사한 자유형 질문
                   (`chat/workflows/domains/field_spec.py` 의 'text' 타입으로 선언).

실행:
    1) `require_fields({'query'})` → 부족 시 MISSING_INPUT.
    2) `retrieve_documents(query)` → Phase 5 single_shot 의 하이브리드 + rerank
       파이프라인을 그대로 재사용. 후보 품질이 LLM 의 셀 선택 정확도에 직결되기
       때문에 raw `search_chunks` 는 쓰지 않는다.
    3) 청크마다 `parse_markdown_tables` 로 GFM 표만 필터. 한 개도 없으면 NOT_FOUND.
    4) 시스템 프롬프트 + 질문 + (document, 표) 리스트를 LLM(`gpt-4o-mini`) 에 전달.
    5) 응답 파싱:
       - `{}` 또는 `answer` 키 없음 → NOT_FOUND.
       - JSON 파싱 실패 / 예기치 못한 예외 → UPSTREAM_ERROR.
       - answer 존재 → OK. details 에 source_document / matched_row / matched_column 보관.
    6) LLM 호출 성공분(성공이든 빈 응답이든) 의 TokenUsage 는 `record_token_usage`.

`UNSUPPORTED` 는 이 workflow 에서 사용하지 않는다 — 미등록 key 는 dispatch 단에서
이미 처리된다.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Optional, Tuple

from chat.services.prompt_loader import load_prompt
from chat.services.single_shot.llm import run_chat_completion
from chat.services.single_shot.postprocess import record_token_usage
from chat.services.single_shot.retrieval import retrieve_documents
from chat.services.single_shot.types import QueryPipelineError
from chat.services.token_purpose import PURPOSE_WORKFLOW_TABLE_LOOKUP
from chat.workflows.core import (
    ValidationResult,
    WorkflowResult,
    parse_markdown_tables,
    require_fields,
    serialize_table,
)
from chat.workflows.domains import registry
from chat.workflows.domains.field_spec import FieldSpec


logger = logging.getLogger(__name__)


WORKFLOW_KEY = 'table_lookup'
_PROMPT_PATH = 'chat/table_lookup.md'

# 한 번에 LLM 에 넣을 수 있는 표의 상한 — 컨텍스트 크기 보호 용도.
_MAX_TABLES_IN_PROMPT = 6


INPUT_SCHEMA = {
    'query': FieldSpec(
        type='text',
        required=True,
        aliases=('query', '질문', '찾을 항목'),
    ),
}


class TableLookupWorkflow:
    """Phase 5 `BaseWorkflow` 프로토콜 구현."""

    def prepare(self, raw: Mapping[str, Any]) -> Mapping[str, Any]:
        query = raw.get('query')
        if isinstance(query, str):
            query = query.strip() or None
        return {'query': query}

    def validate(self, normalized: Mapping[str, Any]) -> ValidationResult:
        return require_fields(normalized, ['query'])

    def execute(self, normalized: Mapping[str, Any]) -> WorkflowResult:
        query: str = normalized['query']

        hits = retrieve_documents(query)

        # 표를 포함하는 청크만 candidate 으로 유지.
        candidates: list[tuple[str, list[dict[str, Any]]]] = []
        for hit in hits:
            tables = parse_markdown_tables(getattr(hit, 'content', '') or '')
            if not tables:
                continue
            filename = _hit_filename(hit)
            candidates.append((filename, tables))
            if len(candidates) >= _MAX_TABLES_IN_PROMPT:
                break

        if not candidates:
            return WorkflowResult.not_found(
                '질문에 맞는 표를 찾지 못했습니다. 관련 문서가 업로드되어 있는지 확인해 주세요.'
            )

        try:
            answer, meta = _ask_llm_for_cell(query, candidates)
        except QueryPipelineError as exc:
            logger.warning('table_lookup LLM 호출 실패: %s', exc)
            return WorkflowResult.upstream_error(
                '표 해석 중 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.'
            )
        except Exception as exc:                                      # noqa: BLE001
            logger.warning('table_lookup LLM 예기치 못한 오류: %s', exc)
            return WorkflowResult.upstream_error(
                '표 해석 중 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.'
            )

        if not answer:
            return WorkflowResult.not_found(
                '표에서 해당 값을 찾지 못했습니다. 항목 이름이나 질문을 조금 바꿔 보세요.'
            )

        return WorkflowResult.ok(
            value=answer,
            details={
                'source_document': meta.get('source_document') or '',
                'matched_row': meta.get('matched_row') or '',
                'matched_column': meta.get('matched_column') or '',
            },
        )


# ---------------------------------------------------------------------------
# LLM 호출
# ---------------------------------------------------------------------------

def _ask_llm_for_cell(
    query: str,
    candidates: list[tuple[str, list[dict[str, Any]]]],
) -> Tuple[Optional[str], dict[str, Any]]:
    """LLM 에 질문 + 표를 보내고 `(answer, meta)` 로 돌려받는다.

    파싱 실패·예외는 호출측이 `upstream_error` 로 번역하도록 raise.
    """
    system_prompt = load_prompt(_PROMPT_PATH)
    user_payload = _format_user_payload(query, candidates)
    messages = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_payload},
    ]

    raw, usage, model = run_chat_completion(messages)

    # LLM 호출이 실제로 돌았으므로 토큰 기록. 성공·빈 응답 상관 없음.
    if usage is not None and model:
        try:
            record_token_usage(model, usage, purpose=PURPOSE_WORKFLOW_TABLE_LOOKUP)
        except Exception as exc:                                      # noqa: BLE001
            # TokenUsage 기록 실패가 답변 자체를 실패시키지 않는다.
            logger.warning('table_lookup TokenUsage 기록 실패: %s', exc)

    parsed = _parse_json_object(raw)
    if parsed is None:
        # 파싱 실패는 수용 가능한 경로가 아니다 — upstream_error 로 번역해야 함.
        raise ValueError(f'table_lookup JSON 파싱 실패: {raw[:200]!r}')

    answer = parsed.get('answer')
    if isinstance(answer, str):
        answer = answer.strip() or None
    elif answer is None:
        pass
    else:
        # 문자열이 아닌 값(숫자 등)도 일단 str 로 보정.
        answer = str(answer).strip() or None

    meta = {
        'source_document': _as_str(parsed.get('source_document')),
        'matched_row': _as_str(parsed.get('matched_row')),
        'matched_column': _as_str(parsed.get('matched_column')),
    }
    return answer, meta


def _format_user_payload(
    query: str,
    candidates: list[tuple[str, list[dict[str, Any]]]],
) -> str:
    lines = [f'Question: {query}', '', 'Tables (markdown):']
    for filename, tables in candidates:
        for table in tables:
            lines.append('')
            lines.append(f'=== Document: {filename} ===')
            lines.append(serialize_table(table))
    lines.append('')
    lines.append('Return JSON only:')
    return '\n'.join(lines)


def _parse_json_object(raw: str) -> Optional[dict[str, Any]]:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith('```'):
        text = text.strip('`')
        if text.lower().startswith('json'):
            text = text[4:]
    start = text.find('{')
    end = text.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _as_str(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _hit_filename(hit: Any) -> str:
    """`ChunkHit` 에서 표시용 파일명을 최대한 방어적으로 추출."""
    for attr in ('document_name', 'original_name', 'filename'):
        value = getattr(hit, attr, None)
        if value:
            return str(value)
    doc = getattr(hit, 'document', None)
    if doc is not None:
        for attr in ('original_name', 'name'):
            value = getattr(doc, attr, None)
            if value:
                return str(value)
    return '(출처 미상)'


registry.register(
    registry.WorkflowEntry(
        key=WORKFLOW_KEY,
        title='표 조회',
        description='업로드된 문서의 표에서 사용자가 묻는 셀 값을 찾아 반환합니다.',
        status=registry.STATUS_STABLE,
        factory=lambda: TableLookupWorkflow(),
        input_schema=INPUT_SCHEMA,
    )
)

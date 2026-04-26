"""Agent 가 처음 쓰는 세 도구 등록 (Phase 7-1).

  - `retrieve_documents` — Phase 6-3 와 같은 reranker 포함 retrieval. schema 모드.
  - `find_canonical_qa` — 과거 승격된 Q&A 임베딩 검색. schema 모드.
  - `run_workflow` — 등록된 generic workflow 를 그대로 실행. raw 모드 (입력 형태가
    `workflow_key` 마다 달라서 schema 로 강제할 수 없음).

각 도구는 callable 결과를 LLM 이 다시 보기 좋은 짧은 한국어 한두 줄로 요약한다.
원본 응답 전체를 다음 iteration 에 올리지 않는다 — 컨텍스트 폭주 방지.
"""

from __future__ import annotations

from typing import Any, Mapping

from chat.services.agent.tools import Tool, register
from chat.services.single_shot.qa_cache import find_canonical_qa as _qa_cache_find
from chat.services.single_shot.retrieval import retrieve_documents as _retrieve
from chat.workflows.core import WorkflowResult
from chat.workflows.domains import dispatch as _workflow_dispatch
from chat.workflows.domains.field_spec import FieldSpec


# ---------------------------------------------------------------------------
# retrieve_documents
# ---------------------------------------------------------------------------

def _retrieve_callable(arguments: Mapping[str, Any]) -> list:
    return _retrieve(arguments['query'])


def _summarize_retrieve(hits: Any) -> str:
    if not hits:
        return '검색 결과 없음 (0건)'
    first = hits[0]
    name = getattr(first, 'document_name', None) or '(출처 미상)'
    snippet = (getattr(first, 'content', '') or '')[:80].replace('\n', ' ')
    return f'{len(hits)}건, 첫 출처: {name} — "{snippet}..."'


# ---------------------------------------------------------------------------
# find_canonical_qa
# ---------------------------------------------------------------------------

def _qa_callable(arguments: Mapping[str, Any]) -> list:
    return _qa_cache_find(arguments['query'])


def _summarize_qa(hits: Any) -> str:
    if not hits:
        return '과거 Q&A 일치 없음 (0건)'
    top = hits[0]
    return (
        f'{len(hits)}건, top similarity={top.similarity:.3f} — '
        f'질문: "{top.question[:60]}..."'
    )


# ---------------------------------------------------------------------------
# run_workflow
# ---------------------------------------------------------------------------

def _workflow_callable(arguments: Mapping[str, Any]) -> WorkflowResult:
    workflow_key = arguments.get('workflow_key') or ''
    workflow_input = arguments.get('input') or {}
    return _workflow_dispatch.run(workflow_key, workflow_input)


def _summarize_workflow(result: Any) -> str:
    """`WorkflowResult` 의 status 와 핵심 값만 한 줄로 요약."""
    if not isinstance(result, WorkflowResult):
        return f'예상치 못한 응답 형식: {type(result).__name__}'
    status = result.status.value
    if result.value is not None:
        return f'status={status}, value={_short_value(result.value)}'
    reason = ''
    if result.details and 'reason' in result.details:
        reason = str(result.details['reason'])[:120]
    if result.missing_fields:
        return f'status={status}, missing={list(result.missing_fields)}'
    return f'status={status}, {reason}' if reason else f'status={status}'


def _short_value(value: Any) -> str:
    if isinstance(value, (int, float, bool)):
        return str(value)
    text = str(value)
    return text if len(text) <= 80 else text[:79] + '…'


# ---------------------------------------------------------------------------
# 등록 — import 부작용
# ---------------------------------------------------------------------------

register(Tool(
    name='retrieve_documents',
    description=(
        '회사 문서 청크를 하이브리드 + reranker 로 검색합니다. '
        'query 는 자유형 한국어/영어 검색어.'
    ),
    input_schema={
        'query': FieldSpec(type='text', required=True, aliases=('query', '검색어')),
    },
    callable=_retrieve_callable,
    summarize=_summarize_retrieve,
))


register(Tool(
    name='find_canonical_qa',
    description=(
        '과거 공식 Q&A 중 유사 질문을 임베딩 거리로 찾습니다. '
        '같은 질문이 이미 답변된 적 있는지 확인할 때.'
    ),
    input_schema={
        'query': FieldSpec(type='text', required=True, aliases=('query', '질문')),
    },
    callable=_qa_callable,
    summarize=_summarize_qa,
))


register(Tool(
    name='run_workflow',
    description=(
        '등록된 generic workflow 를 직접 호출합니다. '
        'arguments 형태: {"workflow_key": "date_calculation|amount_calculation|table_lookup", '
        '"input": {workflow 가 요구하는 입력 dict}}. '
        '잘못된 key/input 은 workflow 가 자체 status 로 알려줍니다.'
    ),
    input_schema=None,   # raw 모드 — workflow_key 마다 input 형태가 달라 schema 강제 X
    callable=_workflow_callable,
    summarize=_summarize_workflow,
))

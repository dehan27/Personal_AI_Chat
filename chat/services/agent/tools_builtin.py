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


_RETRIEVE_TOP_N = 3
# Phase 7-2 smoke: 180자는 표 헤더 정도밖에 못 담아 LLM 이 본문을 못 봄. 400자면
# 표 4~6 행 / 두세 단락이 들어가 비교형 질문에 답을 만들 수 있다. 임의 cap 인 건
# 변함없으므로 프롬프트에 "스니펫에 값 없으면 query 다듬어 다시 retrieve" 도
# 같이 명시 — ReAct loop 의 본래 의도대로 LLM 이 보강 검색을 하도록.
_RETRIEVE_SNIPPET_LEN = 400


def _summarize_retrieve(hits: Any) -> str:
    """top N 청크의 출처 + 본문 일부를 LLM 이 실제로 답을 만들 수 있는 분량으로 노출.

    Phase 7-1 초기에는 첫 청크 80자만 노출했더니 "비교" 류 질문에서 데이터를
    찾아놓고도 LLM 이 표 값을 못 봐서 "자료를 찾지 못했습니다" 로 종결되는 회귀가
    났음 (Phase 7-2 smoke). top 3 청크 * ~180자 = 약 540자로 늘려 LLM 이 본문을
    보고 비교/요약을 할 수 있도록 한다. 총 길이는 `MAX_OBSERVATION_SUMMARY_CHARS`
    (600) 한도 안.
    """
    if not hits:
        return '검색 결과 없음 (0건)'

    parts = [f'{len(hits)}건 검색됨:']
    for idx, hit in enumerate(hits[:_RETRIEVE_TOP_N], start=1):
        name = getattr(hit, 'document_name', None) or '(출처 미상)'
        content = (getattr(hit, 'content', '') or '').replace('\n', ' ').strip()
        snippet = content[:_RETRIEVE_SNIPPET_LEN]
        if len(content) > _RETRIEVE_SNIPPET_LEN:
            snippet += '…'
        parts.append(f'[{idx}] {name}: "{snippet}"')
    if len(hits) > _RETRIEVE_TOP_N:
        parts.append(f'(이하 {len(hits) - _RETRIEVE_TOP_N}건 생략)')
    return ' '.join(parts)


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

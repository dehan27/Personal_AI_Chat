"""Agent 가 처음 쓰는 세 도구 등록 (Phase 7-1).

  - `retrieve_documents` — Phase 6-3 와 같은 reranker 포함 retrieval. schema 모드.
  - `find_canonical_qa` — 과거 승격된 Q&A 임베딩 검색. schema 모드.
  - `run_workflow` — 등록된 generic workflow 를 그대로 실행. raw 모드 (입력 형태가
    `workflow_key` 마다 달라서 schema 로 강제할 수 없음).

각 도구는 callable 결과를 LLM 이 다시 보기 좋은 짧은 한국어 한두 줄로 요약한다.
원본 응답 전체를 다음 iteration 에 올리지 않는다 — 컨텍스트 폭주 방지.
"""

from __future__ import annotations

import string
from typing import Any, List, Mapping

from chat.services.agent.tools import Tool, register
from chat.services.single_shot.qa_cache import find_canonical_qa as _qa_cache_find
from chat.services.single_shot.retrieval import retrieve_documents as _retrieve
from chat.workflows.core import WorkflowResult
from chat.workflows.domains import dispatch as _workflow_dispatch
from chat.workflows.domains.field_spec import FieldSpec


# ---------------------------------------------------------------------------
# Phase 7-3: query-focused snippet windowing helpers
# ---------------------------------------------------------------------------

# 토큰 양 끝에 흔히 붙는 문장부호 — strip 대상.
# ASCII punctuation + 한국어 콤마/물음표/문장 부호.
_TOKEN_STRIP_CHARS = string.punctuation + '·、，。？！'

# 한국어 1자 조사/어미/단일 stopword 제거. 영문은 'a', 'I' 등 단일 문자도 같은 이유로 컷.
_KEYWORD_MIN_LEN = 2


def _tokenize_query(query: str) -> List[str]:
    """Query 를 윈도우 매칭용 토큰으로 분리 (Phase 7-3).

    - 공백 split.
    - 토큰 양 끝 punctuation strip (`결혼?` → `결혼`, `"경조금"` → `경조금`).
    - 길이 ≥ 2 만 유지.
    - **길이 내림차순 정렬** — 긴 토큰일수록 도메인 키워드일 확률이 높음.
      매치 시 긴 토큰 위치 우선 → 일반 토큰 ("비교", "있는", "하는") 이 청크
      앞부분에 우연히 걸려 관련 없는 윈도우를 고르는 회귀 차단.

    한국어 형태소 분석기 (KoNLPy / Mecab) 미도입 — 의존성 비용 vs 효용. 운영
    데이터에서 부족이 입증되면 후속 Phase 에서 검토.
    """
    if not query:
        return []
    tokens: List[str] = []
    for raw in query.split():
        cleaned = raw.strip(_TOKEN_STRIP_CHARS)
        if len(cleaned) >= _KEYWORD_MIN_LEN:
            tokens.append(cleaned)
    # Python sort 는 stable — 같은 길이 토큰은 입력 순서 유지.
    tokens.sort(key=len, reverse=True)
    return tokens


def _focus_window(content: str, query: str, *, length: int) -> str:
    """Query 키워드 매치 위치 주변 forward-bias 윈도우. 미매치면 첫 N자 fallback.

    윈도우 정책: 매치 위치 기준 앞 1/4 + 뒤 3/4. `start = max(0, earliest -
    length//4)` 의 자연 클램프 덕분에 매치가 청크 매우 앞 (`< length//4`) 이면
    자동으로 `start=0` → 첫 N자 출력 = 7-2 fallback 과 byte-identical.

    의도적으로 `earliest < length` 강제 분기를 두지 않는다 — 그건 "키워드는
    350자, 값은 450자" 같은 흔한 표 패턴에서 본 목적 (401자+ 답 노출) 을 깨뜨린다.
    forward-bias 한 줄기로 처리하면 매치 위치별로 자연스럽게:
        earliest < length//4   → start=0 (7-2 byte-identical)
        length//4 ≤ earliest   → 매치 주변 ±윈도우 (7-3 의 본 가치)
    """
    if not content:
        return ''
    if len(content) <= length:
        return content
    if not query:
        return content[:length] + '…'

    tokens = _tokenize_query(query)
    if not tokens:
        return content[:length] + '…'

    # 긴 토큰부터 매치 — 정렬 덕분에 첫 매치 = 가장 긴 매치된 토큰의 위치.
    lower = content.lower()
    earliest = -1
    for token in tokens:
        idx = lower.find(token.lower())
        if idx >= 0:
            earliest = idx
            break

    if earliest < 0:
        # 미매치 — 청크 첫 N자 fallback (7-2 동작과 동일).
        return content[:length] + '…'

    # forward-bias: 앞 1/4 + 뒤 3/4. 표 행은 매치 위치 다음에 값/단위가 옴.
    pre = length // 4
    start = max(0, earliest - pre)
    end = min(len(content), start + length)
    # content 끝에 닿으면 start 를 뒤로 당겨 윈도우 길이 보존.
    start = max(0, end - length)

    snippet = content[start:end]
    prefix = '…' if start > 0 else ''
    suffix = '…' if end < len(content) else ''
    return prefix + snippet + suffix


# ---------------------------------------------------------------------------
# retrieve_documents
# ---------------------------------------------------------------------------

def _retrieve_callable(arguments: Mapping[str, Any]) -> dict:
    """retrieve_documents tool callable.

    Phase 7-3 부터 반환을 `{'query': ..., 'hits': [...]}` dict 로 감싼다 — query
    를 `_summarize_retrieve` 까지 흘려 keyword-aware windowing 을 가능하게 하기
    위한 우회. `Tool.summarize: Callable[[Any], str]` 시그니처는 그대로 둬서 다른
    도구 / 외부 코드 영향 없음. 이 dict 는 `tools.call` 내부에서 summarize 직전에만
    보이고, 외부에 노출되는 건 `Observation.summary` 문자열 뿐이라 외부 계약 변경 0.
    """
    query = arguments['query']
    return {
        'query': query,
        'hits': _retrieve(query),
    }


_RETRIEVE_TOP_N = 3
# Phase 7-2 smoke: 180자는 표 헤더 정도밖에 못 담아 LLM 이 본문을 못 봄. 400자면
# 표 4~6 행 / 두세 단락이 들어가 비교형 질문에 답을 만들 수 있다. Phase 7-3 부터는
# 이 400자가 청크 첫 N자가 아니라 query 키워드 매치 위치 주변의 윈도우 길이 — 즉
# 위치는 가변, 길이만 고정.
_RETRIEVE_SNIPPET_LEN = 400


def _summarize_retrieve(result: Any) -> str:
    """top N 청크의 출처 + query 키워드 주변 윈도우를 LLM 이 실제 답을 만들 수 있는
    분량으로 노출 (Phase 7-3).

    Phase 7-1: 첫 청크 80자만 → "자료 찾지 못했음" 회귀.
    Phase 7-2: top 3 청크 × 첫 400자. 답이 401자+ 위치에 있으면 못 봄.
    Phase 7-3: top 3 청크 × query 매치 주변 ±400자 forward-bias 윈도우. 미매치
        시 첫 400자 fallback (= 7-2 동작과 동일).

    `result` 는 `_retrieve_callable` 이 만든 `{'query': ..., 'hits': [...]}` dict.
    """
    query = (result or {}).get('query') or ''
    hits = (result or {}).get('hits') or []
    if not hits:
        return '검색 결과 없음 (0건)'

    parts = [f'{len(hits)}건 검색됨:']
    for idx, hit in enumerate(hits[:_RETRIEVE_TOP_N], start=1):
        name = getattr(hit, 'document_name', None) or '(출처 미상)'
        content = (getattr(hit, 'content', '') or '').replace('\n', ' ').strip()
        snippet = _focus_window(content, query, length=_RETRIEVE_SNIPPET_LEN)
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

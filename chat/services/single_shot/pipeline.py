"""Single-shot 조합자.

rewrite → retrieve → qa_cache → prompting → llm → postprocess 순서로
helper 들을 엮어 하나의 질문에 대한 `QueryResult` 를 만든다. 외부 진입점은
`run_single_shot` 하나. view / graph 노드 / 향후 workflow 에서 모두 이 함수를
호출한다.

Phase 4-3: retrieval 앞단에 쿼리 재작성(rewrite) 단계가 붙는다. "비싼거"
같이 맥락에 의존하는 후속 질문을 직전 대화 내용과 함께 cheap LLM 에 보내
self-contained 검색어로 바꾼 뒤, 그 결과를 retrieve_documents /
find_canonical_qa 에 넘긴다. 원본 `question` 은 그대로 LLM 프롬프트와
ChatLog 에 흐른다 — 사용자가 입력한 문구를 보존한다.
"""

from typing import Dict, List, Optional

from chat.services.query_rewriter import rewrite_query_with_history
from chat.services.single_shot.llm import run_chat_completion
from chat.services.single_shot.postprocess import (
    build_sources,
    classify_reply,
    persist_chat_log,
    record_token_usage,
)
from chat.services.single_shot.prompting import build_single_shot_messages
from chat.services.single_shot.qa_cache import find_canonical_qa, resolve_cache_hit
from chat.services.single_shot.retrieval import retrieve_documents
from chat.services.single_shot.types import QueryResult
from chat.services.token_purpose import (
    PURPOSE_QUERY_REWRITER,
    PURPOSE_SINGLE_SHOT_ANSWER,
)


def run_single_shot(
    question: str,
    history: Optional[List[Dict]] = None,
) -> QueryResult:
    """질문 하나를 single-shot 경로로 처리해 QueryResult 를 반환.

    실패 시 `QueryPipelineError` 를 raise 한다. 호출자는 항상 같은 예외만
    포착하면 된다 (graph 노드의 state.error 또는 view 의 502 매핑).
    """
    history = history or []

    # 0) 검색어 재작성 — 맥락 의존 후속 질문을 self-contained 쿼리로 변환.
    #    history 가 비어있거나 LLM 이 실패하면 원본 질문이 그대로 돌아온다.
    search_query, rewriter_usage, rewriter_model = rewrite_query_with_history(
        question, history,
    )
    # 재작성 호출이 실제로 일어났다면 본 LLM 호출과 구분해 별도 레코드로 남긴다.
    # 재작성 실패 / history 빈 경로에서는 usage 가 None 이라 기록하지 않는다.
    if rewriter_usage is not None and rewriter_model is not None:
        record_token_usage(
            rewriter_model, rewriter_usage,
            purpose=PURPOSE_QUERY_REWRITER,
        )

    # 1~2) 자료 후보 검색 + 재정렬
    chunk_hits = retrieve_documents(search_query)

    # 3) 공식 Q&A 검색
    qa_hits = find_canonical_qa(search_query)

    # 4) 캐시 히트면 즉시 반환 (OpenAI 호출 생략)
    cached = resolve_cache_hit(qa_hits)
    if cached is not None:
        return cached

    # 5) 프롬프트 조립
    messages = build_single_shot_messages(question, chunk_hits, qa_hits, history)

    # 6) OpenAI 호출
    reply, usage, model = run_chat_completion(messages)

    # 7) 후처리: 토큰 기록 → 응답 분류 → sources/ChatLog 구성
    record_token_usage(model, usage, purpose=PURPOSE_SINGLE_SHOT_ANSWER)

    is_no_info, is_casual = classify_reply(reply)

    saved_chat_log_id: Optional[int] = None
    sources: List[Dict] = []
    if chunk_hits and not is_no_info and not is_casual:
        saved_chat_log_id = persist_chat_log(question, reply, chunk_hits)
        sources = build_sources(chunk_hits)

    return QueryResult(
        reply=reply,
        sources=sources,
        total_tokens=usage.total_tokens,
        chat_log_id=saved_chat_log_id,
    )

"""질문을 받아 답변까지 내는 전체 쿼리 파이프라인.

흐름:
  1) DocumentChunk 검색 (회사 자료, 하이브리드 + 재정렬)
  2) CanonicalQA 검색 (공식 Q&A 참고)
  3) 프롬프트 조립
  4) OpenAI chat completion 호출
  5) TokenUsage 로그 저장
  6) ChatLog 저장 (자료 기반 답변일 때만 — 피드백·검수 대상)
"""

import logging
from typing import Dict, List, Optional

from chat.models import TokenUsage
from chat.services.qa_retriever import save_chat_log
from chat.services.single_shot.llm import run_chat_completion
from chat.services.single_shot.prompting import build_single_shot_messages
from chat.services.single_shot.qa_cache import find_canonical_qa, resolve_cache_hit
from chat.services.single_shot.retrieval import retrieve_documents
from chat.services.single_shot.types import QueryPipelineError, QueryResult


logger = logging.getLogger(__name__)


# 검색 설정은 각 helper 로 이전됨:
#   - 청크 상수: chat.services.single_shot.retrieval
#   - QA 상수:   chat.services.single_shot.qa_cache

# GPT 답변이 "자료에 없음" 응답인지 판별하는 패턴
_NO_INFO_MARKERS = (
    '회사 자료에 해당 정보가 없습니다',
    '회사 자료에 관련 정보가 없',
    '자료에서 확인되지 않',
    '자료에 관련 정보가 없',
)

# 잡담·인사 응답 판별용 패턴 (짧은 응답과 함께 체크)
_CASUAL_MARKERS = (
    '안녕하세요', '안녕!', '반갑습니다', '좋은 하루',
    '무엇을 도와', '어떻게 도와',
)
_CASUAL_MAX_LEN = 80  # 이 길이 미만이면서 잡담 패턴이 있으면 잡담으로 판정


def _is_no_info_reply(reply: str) -> bool:
    """GPT 응답이 '자료 없음' 패턴인지 체크."""
    return any(marker in reply for marker in _NO_INFO_MARKERS)


def _is_casual_reply(reply: str) -> bool:
    """GPT 응답이 잡담·인사성인지 체크 (출처·피드백 숨김 대상)."""
    if len(reply) > _CASUAL_MAX_LEN:
        return False
    return any(marker in reply for marker in _CASUAL_MARKERS)


def answer_question(
    question: str,
    history: Optional[List[Dict]] = None,
) -> QueryResult:
    history = history or []

    # 1~2) 자료 후보 검색 + 재정렬
    chunk_hits = retrieve_documents(question)

    # 3) 공식 Q&A 검색
    qa_hits = find_canonical_qa(question)

    # 4) 캐시 히트면 즉시 반환 (OpenAI 호출 생략)
    cached = resolve_cache_hit(qa_hits)
    if cached is not None:
        return cached

    # 5) 프롬프트 조립
    messages = build_single_shot_messages(question, chunk_hits, qa_hits, history)

    # 6) OpenAI 호출
    reply, usage, model = run_chat_completion(messages)

    # 5) 토큰 사용 로그 (모든 호출에 대해 기록)
    TokenUsage.objects.create(
        model=model,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
    )

    # 잡담·자료 없음 응답이면 sources·ChatLog 모두 스킵
    # (자료 기반 답변이 아니라 UI에 출처·피드백 버튼 띄우면 부자연스러움)
    is_no_info = _is_no_info_reply(reply)
    is_casual = _is_casual_reply(reply)

    saved_chat_log_id: Optional[int] = None
    sources: List[Dict] = []

    if chunk_hits and not is_no_info and not is_casual:
        # 자료 기반 답변 — ChatLog 저장 + 출처 반환
        source_ids = sorted({h.document_id for h in chunk_hits})
        try:
            cl = save_chat_log(question, reply, sources=source_ids)
            saved_chat_log_id = cl.pk
        except Exception as e:
            logger.warning('ChatLog 저장 실패: %s', e)

        seen_ids = set()
        for h in chunk_hits:
            if h.document_id in seen_ids:
                continue
            seen_ids.add(h.document_id)
            sources.append({'name': h.document_name, 'url': h.document_url})

    return QueryResult(
        reply=reply,
        sources=sources,
        total_tokens=usage.total_tokens,
        chat_log_id=saved_chat_log_id,
    )

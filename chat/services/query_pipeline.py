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
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from openai import OpenAI

from chat.models import CanonicalQA, TokenUsage
from chat.services.prompt_builder import build_messages
from chat.services.qa_retriever import save_chat_log, search_canonical_qa
from chat.services.reranker import rerank
from files.models import Document
from files.services.retriever import search_chunks


logger = logging.getLogger(__name__)


# 검색 설정
CHUNK_CANDIDATES = 10
CHUNK_TOP_K = 5

QA_TOP_K = 3
QA_SIMILARITY_THRESHOLD = 0.80

# 공식 Q&A 캐시 히트 기준 — 이 이상 유사하면 OpenAI 호출 없이 그 답변을 그대로 반환
# 낮출수록 캐시 적중률↑ (일관성·속도·비용 ↑) / 너무 낮추면 다른 질문에도 같은 답 나갈 위험
QA_CACHE_HIT_THRESHOLD = 0.88

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


@dataclass
class QueryResult:
    """파이프라인 출력."""
    reply: str
    sources: List[Dict]                 # [{name, url}, ...]
    total_tokens: int
    chat_log_id: Optional[int] = None   # 저장된 ChatLog id (피드백 용)


class QueryPipelineError(Exception):
    """쿼리 파이프라인 실패."""


def answer_question(
    question: str,
    history: Optional[List[Dict]] = None,
) -> QueryResult:
    history = history or []

    # 1) 자료 후보 검색 + 재정렬
    candidates = search_chunks(question, top_k=CHUNK_CANDIDATES)
    logger.info('후보 검색: %d개 (질문: %s)', len(candidates), question[:30])
    chunk_hits = rerank(question, candidates, top_k=CHUNK_TOP_K)
    logger.info('재정렬 후 선택: %d개', len(chunk_hits))

    # 2) 공식 Q&A 검색
    qa_hits = search_canonical_qa(
        question,
        top_k=QA_TOP_K,
        similarity_threshold=QA_SIMILARITY_THRESHOLD,
    )
    logger.info('CanonicalQA 검색: %d개', len(qa_hits))

    # 2-1) 캐시 히트 — 거의 동일한 공식 질문이 있으면 OpenAI 생략
    if qa_hits and qa_hits[0].similarity >= QA_CACHE_HIT_THRESHOLD:
        hit = qa_hits[0]
        logger.info('CanonicalQA 캐시 히트 (sim=%.3f, qa_id=%d)', hit.similarity, hit.qa_id)
        canonical = CanonicalQA.objects.filter(pk=hit.qa_id).first()
        cached_sources: List[Dict] = []
        if canonical and canonical.sources:
            for d in Document.objects.filter(pk__in=canonical.sources):
                cached_sources.append({
                    'name': d.original_name,
                    'url': d.file.url if d.file else '',
                })
        return QueryResult(
            reply=hit.answer,
            sources=cached_sources,
            total_tokens=0,       # OpenAI 호출 없음
            chat_log_id=None,     # 재사용 응답은 ChatLog 생성 X
        )

    # 3) 프롬프트 조립
    messages = build_messages(question, chunk_hits, qa_hits, history)

    # 4) OpenAI 호출
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        raise QueryPipelineError('OPENAI_API_KEY가 설정되지 않았습니다.')
    model = os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')

    try:
        client = OpenAI(api_key=api_key)
        completion = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,   # 동일 질문엔 동일 답변이 나오도록 (사실·수치 중심 챗봇)
        )
    except Exception as e:
        raise QueryPipelineError(f'OpenAI 호출 실패: {e}') from e

    reply = completion.choices[0].message.content or ''
    usage = completion.usage

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

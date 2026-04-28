"""단계 7: 후처리 + side effect.

single-shot 의 LLM 호출 이후에 반복되는 네 가지 일:
  1) 응답 텍스트를 분류 (no-info / casual) — UI 에 출처·피드백 표시할지 결정
  2) TokenUsage 로그 기록
  3) 자료 기반 답변일 때 ChatLog 저장
  4) sources 리스트 구성 (중복 제거)

모든 DB 쓰기는 이 모듈에서만. 다른 helper 는 순수 함수로 둔다.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from chat.models import TokenUsage
from chat.services.qa_retriever import save_chat_log
from chat.services.token_purpose import PURPOSE_UNKNOWN, validate_purpose
from files.services.retriever import ChunkHit


logger = logging.getLogger(__name__)


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


def classify_reply(reply: str) -> Tuple[bool, bool]:
    """응답 텍스트를 (is_no_info, is_casual) 로 분류.

    - is_no_info: '자료에 해당 정보가 없습니다' 계열 문구 매칭
    - is_casual: 80자 이내 + 인사/도움말 패턴 매칭
    두 값 중 하나라도 True 면 출처·피드백·ChatLog 저장을 모두 스킵.
    """
    is_no_info = any(marker in reply for marker in _NO_INFO_MARKERS)
    is_casual = (
        len(reply) <= _CASUAL_MAX_LEN
        and any(marker in reply for marker in _CASUAL_MARKERS)
    )
    return is_no_info, is_casual


def record_token_usage(
    model: str,
    usage: Any,
    *,
    purpose: str = PURPOSE_UNKNOWN,
) -> None:
    """OpenAI 응답의 usage 를 TokenUsage 테이블에 한 행으로 기록.

    usage 는 OpenAI SDK 의 CompletionUsage 호환 객체 (prompt_tokens /
    completion_tokens / total_tokens 속성). 캐시 히트 등 호출이 없었던
    경로에서는 이 함수를 호출하지 않는다.

    Phase 8-2: keyword-only `purpose` 추가. 기존 `record_token_usage(model, usage)`
    호출 호환 (default 'unknown'). `validate_purpose` 가 알 수 없는 값을
    'unknown' 으로 절감 — 호출부 오타가 데이터 오염으로 직결되지 않게.
    """
    TokenUsage.objects.create(
        model=model,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        purpose=validate_purpose(purpose),
    )


def persist_chat_log(
    question: str,
    reply: str,
    chunk_hits: List[ChunkHit],
) -> Optional[int]:
    """자료 기반 답변에 대해 ChatLog 를 저장하고 pk 를 반환.

    실패는 로그만 남기고 None 반환 — 저장 실패가 응답 자체를 막지 않도록.
    호출자는 이 함수를 부를지 여부(자료 있음 + no-info 아님 + casual 아님)를
    판단해서 건네준다.
    """
    source_ids = sorted({h.document_id for h in chunk_hits})
    try:
        cl = save_chat_log(question, reply, sources=source_ids)
        return cl.pk
    except Exception as exc:
        logger.warning('ChatLog 저장 실패: %s', exc)
        return None


def build_sources(chunk_hits: List[ChunkHit]) -> List[Dict]:
    """chunk_hits 의 document_id 기준으로 중복을 제거한 sources 리스트.

    UI 에 표시할 포맷은 `{'name': 원본파일명, 'url': 미디어URL}`.
    """
    sources: List[Dict] = []
    seen_ids = set()
    for h in chunk_hits:
        if h.document_id in seen_ids:
            continue
        seen_ids.add(h.document_id)
        sources.append({'name': h.document_name, 'url': h.document_url})
    return sources

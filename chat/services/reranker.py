"""하이브리드 검색 결과를 LLM으로 재정렬하는 모듈.

벡터·키워드 검색이 뽑은 top 10 후보를 GPT-4o-mini에 한꺼번에 보내고,
"질문과 가장 관련 있는 순"으로 순위를 다시 매기게 한다.

단일 API 호출로 끝나므로 비용·지연 부담이 작다.
"""

import json
import logging
import os
from typing import List

from openai import OpenAI

from files.services.retriever import ChunkHit


logger = logging.getLogger(__name__)


RERANK_MODEL = 'gpt-4o-mini'

# 청크 내용을 프롬프트에 넣을 때 자르는 길이 (토큰 절약)
MAX_CONTENT_CHARS = 600


class RerankError(Exception):
    pass


def rerank(question: str, candidates: List[ChunkHit], top_k: int = 5) -> List[ChunkHit]:
    """후보 청크들을 질문 관련성 기준으로 재정렬.

    Args:
        question: 사용자 질문
        candidates: 하이브리드 검색 결과 (보통 10개)
        top_k: 최종 반환 개수

    Returns:
        재정렬된 ChunkHit 리스트. 상위 top_k개.
        재정렬 실패 시 입력 순서 유지한 top_k.
    """
    if not candidates:
        return []
    if len(candidates) <= top_k:
        return candidates

    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        logger.warning('OPENAI_API_KEY 없음 → 재정렬 스킵')
        return candidates[:top_k]

    # 프롬프트 구성 — 후보마다 번호 부여
    numbered = []
    for i, hit in enumerate(candidates):
        content = hit.content[:MAX_CONTENT_CHARS].replace('\n', ' ')
        numbered.append(f'[{i}] {content}')
    candidates_block = '\n\n'.join(numbered)

    prompt = (
        f'질문에 대한 검색 결과 중에서 가장 관련 있는 순서대로 번호를 나열해줘.\n'
        f'관련 없는 건 제외해도 된다.\n'
        f'응답은 반드시 JSON 형식: {{"ranking": [번호1, 번호2, ...]}}\n'
        f'\n'
        f'=== 질문 ===\n{question}\n'
        f'\n'
        f'=== 후보 ===\n{candidates_block}\n'
    )

    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=RERANK_MODEL,
            messages=[{'role': 'user', 'content': prompt}],
            response_format={'type': 'json_object'},
            temperature=0,
        )
        raw = resp.choices[0].message.content or '{}'
        parsed = json.loads(raw)
        ranking = parsed.get('ranking', [])
    except Exception as e:
        # 재정렬 실패해도 답변 흐름은 유지 (입력 순서로 폴백)
        logger.warning('재정렬 실패, 폴백: %s', e)
        return candidates[:top_k]

    # 유효한 인덱스만 필터링 + 중복 제거
    seen = set()
    reordered: List[ChunkHit] = []
    for idx in ranking:
        if isinstance(idx, int) and 0 <= idx < len(candidates) and idx not in seen:
            reordered.append(candidates[idx])
            seen.add(idx)

    if not reordered:
        return candidates[:top_k]

    return reordered[:top_k]

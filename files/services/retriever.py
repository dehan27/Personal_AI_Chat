"""질문 → DocumentChunk 하이브리드 검색.

- 벡터 검색: pgvector 코사인 거리 (의미 유사도)
- 키워드 검색: 질문에서 뽑은 단어들로 ILIKE 매칭 + 매칭 수 기반 랭킹

두 결과를 Reciprocal Rank Fusion(RRF)으로 병합해 top-K 반환.
"""

import re
from dataclasses import dataclass
from typing import Dict, List

from django.db.models import Case, IntegerField, Q, Sum, Value, When
from pgvector.django import CosineDistance

from files.models import DocumentChunk
from files.services.embedder import embed_text


# RRF 점수 상수 (표준 값)
RRF_K = 60

# 각 단일 검색에서 뽑을 후보 수
VECTOR_POOL_SIZE = 20
KEYWORD_POOL_SIZE = 20

# 최종 기본 반환 개수
DEFAULT_TOP_K = 5

# 질문 토큰에서 제외할 조사·어미·의문사
_STOPWORDS = {
    '뭐야', '뭔가요', '뭐예요', '어디', '어디야', '언제', '누가', '얼마나',
    '어떻게', '왜', '어떤', '무엇', '뭔지', '알려줘', '알려주세요',
    '입니까', '입니다', '있나요', '있어요', '이야', '야',
    '은', '는', '이', '가', '을', '를', '의', '에', '와', '과', '로',
    '해', '해줘', '해주세요', '그럼',
}


@dataclass
class ChunkHit:
    chunk_id: int
    document_id: int
    document_name: str
    document_url: str   # 원본 파일 서빙 URL (/media/origin/xxx)
    content: str
    score: float        # RRF 점수 (높을수록 관련)


def search_chunks(question: str, top_k: int = DEFAULT_TOP_K) -> List[ChunkHit]:
    """하이브리드 검색 top-K."""
    if not question.strip():
        return []

    # --- 1) 벡터 검색 ---
    q_vec = embed_text(question)
    vector_ids = list(
        DocumentChunk.objects
        .annotate(distance=CosineDistance('embedding', q_vec))
        .order_by('distance')
        .values_list('id', flat=True)[:VECTOR_POOL_SIZE]
    )

    # --- 2) 키워드 검색 ---
    keywords = _extract_keywords(question)
    keyword_ids: List[int] = []
    if keywords:
        # Q 객체로 OR 조합 + 매칭 키워드 수로 정렬
        q_filter = Q()
        for kw in keywords:
            q_filter |= Q(content__icontains=kw)

        # 각 키워드에 대해 포함 여부를 0/1로 합산 → 매칭 수
        match_count_expr = Sum(
            sum(
                (
                    Case(
                        When(content__icontains=kw, then=Value(1)),
                        default=Value(0),
                        output_field=IntegerField(),
                    )
                    for kw in keywords
                ),
                start=Value(0, output_field=IntegerField()),
            )
        )
        # 위 Sum은 잘못된 조합이라 간단 버전으로 대체
        # (각 키워드별 Case를 annotate로 더함)
        qs = DocumentChunk.objects.filter(q_filter)
        for i, kw in enumerate(keywords):
            qs = qs.annotate(
                **{
                    f'_hit_{i}': Case(
                        When(content__icontains=kw, then=Value(1)),
                        default=Value(0),
                        output_field=IntegerField(),
                    )
                }
            )
        # 총합을 hit_total 필드로
        from django.db.models import F
        total_expr = None
        for i in range(len(keywords)):
            col = F(f'_hit_{i}')
            total_expr = col if total_expr is None else total_expr + col
        qs = qs.annotate(hit_total=total_expr).order_by('-hit_total')

        keyword_ids = list(qs.values_list('id', flat=True)[:KEYWORD_POOL_SIZE])

    # --- 3) RRF 병합 ---
    rrf_scores: Dict[int, float] = {}
    for rank, cid in enumerate(vector_ids, start=1):
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (RRF_K + rank)
    for rank, cid in enumerate(keyword_ids, start=1):
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (RRF_K + rank)

    if not rrf_scores:
        return []

    # --- 4) top_k id 뽑기 ---
    top_ids = sorted(rrf_scores.keys(), key=lambda cid: rrf_scores[cid], reverse=True)[:top_k]

    # --- 5) 객체 조회 + 순서 보존 ---
    chunks_by_id = {
        c.id: c
        for c in DocumentChunk.objects.filter(id__in=top_ids).select_related('document')
    }
    hits: List[ChunkHit] = []
    for cid in top_ids:
        c = chunks_by_id.get(cid)
        if not c:
            continue
        hits.append(ChunkHit(
            chunk_id=c.id,
            document_id=c.document_id,
            document_name=c.document.original_name,
            document_url=c.document.file.url if c.document.file else '',
            content=c.content,
            score=rrf_scores[cid],
        ))
    return hits


def _extract_keywords(question: str) -> List[str]:
    """질문에서 의미 있는 단어들 추출.

    한국어 간단 토큰화: 공백·특수문자 제거, 2글자 이상, 불용어 제거.
    """
    # 특수문자 제거 후 공백 기준 분리
    tokens = re.findall(r'[가-힣A-Za-z0-9]+', question)
    # 2글자 이상 & 불용어 제외
    keywords = [t for t in tokens if len(t) >= 2 and t not in _STOPWORDS]
    # 중복 제거 (순서 유지)
    seen = set()
    uniq = []
    for kw in keywords:
        if kw not in seen:
            uniq.append(kw)
            seen.add(kw)
    return uniq

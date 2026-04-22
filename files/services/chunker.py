
"""긴 텍스트를 임베딩 가능한 크기로 잘게 쪼개는 모듈.

핵심 아이디어:
- 토큰(tiktoken) 기준으로 자름 → 임베딩 모델과 단위가 일치
- 가능하면 문단(\\n\\n) → 줄(\\n) → 문장(. ) 경계를 살리도록 재귀적으로 쪼갬
- 청크 사이에 오버랩을 둠 → 문맥 끊김 방지
"""

from typing import List

import tiktoken


# OpenAI 임베딩 모델 기준 (gpt-4 계열과 호환되는 cl100k_base 인코딩 사용)
# text-embedding-3-small/large 둘 다 cl100k_base 호환
_ENCODER = tiktoken.get_encoding('cl100k_base')

# 기본 청크 설정
DEFAULT_CHUNK_TOKENS = 500
DEFAULT_OVERLAP_TOKENS = 100


def count_tokens(text: str) -> int:
    """토큰 개수 세기."""
    return len(_ENCODER.encode(text))


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_TOKENS,
    overlap: int = DEFAULT_OVERLAP_TOKENS,
) -> List[str]:
    """텍스트를 청크 리스트로 쪼갠다.

    Args:
        text: 원본 텍스트
        chunk_size: 한 청크의 최대 토큰 수
        overlap: 청크 사이 겹침 토큰 수

    Returns:
        청크 문자열의 리스트 (각 청크는 대략 chunk_size 토큰)
    """
    if overlap >= chunk_size:
        raise ValueError('overlap은 chunk_size보다 작아야 합니다.')

    # 1단계: 의미 경계로 먼저 쪼갬 (문단 → 줄 → 문장 → 단어 순)
    segments = _split_with_separators(text)

    # 2단계: 토큰 기준으로 이어붙여서 청크 구성
    chunks: List[str] = []
    current_tokens: List[int] = []

    for seg in segments:
        seg_tokens = _ENCODER.encode(seg)

        # 세그먼트 하나가 chunk_size보다 큰 경우는 강제로 쪼갬
        if len(seg_tokens) > chunk_size:
            # 현재 쌓인 게 있으면 먼저 마감
            if current_tokens:
                chunks.append(_ENCODER.decode(current_tokens))
                current_tokens = _tail_overlap(current_tokens, overlap)
            # 큰 세그먼트를 chunk_size 단위로 강제 분할
            for i in range(0, len(seg_tokens), chunk_size - overlap):
                piece = seg_tokens[i:i + chunk_size]
                chunks.append(_ENCODER.decode(piece))
            continue

        # 현재 청크에 더 넣어도 크기 안 초과하면 계속 쌓음
        if len(current_tokens) + len(seg_tokens) <= chunk_size:
            current_tokens.extend(seg_tokens)
        else:
            # 초과하면 현재 청크 마감 + 오버랩 남기고 새 청크 시작
            chunks.append(_ENCODER.decode(current_tokens))
            current_tokens = _tail_overlap(current_tokens, overlap)
            current_tokens.extend(seg_tokens)

    # 마지막 남은 청크 추가
    if current_tokens:
        chunks.append(_ENCODER.decode(current_tokens))

    return [c.strip() for c in chunks if c.strip()]


def _split_with_separators(text: str) -> List[str]:
    """문단 → 줄 → 문장 → 단어 순서로 의미 단위 세그먼트로 쪼갠다."""
    # 먼저 문단(빈 줄) 기준
    paragraphs = [p for p in text.split('\n\n') if p.strip()]

    segments: List[str] = []
    for p in paragraphs:
        # 문단 하나가 너무 길면 줄 단위로 더 쪼갬
        if count_tokens(p) <= DEFAULT_CHUNK_TOKENS:
            segments.append(p)
        else:
            for line in p.split('\n'):
                if line.strip():
                    # 줄도 너무 길면 문장 단위로 추가 분리
                    if count_tokens(line) <= DEFAULT_CHUNK_TOKENS:
                        segments.append(line)
                    else:
                        segments.extend(s for s in line.split('. ') if s.strip())
    return segments


def _tail_overlap(tokens: List[int], overlap: int) -> List[int]:
    """청크의 뒤쪽 overlap 만큼을 다음 청크 시작에 넘겨주기 위해 잘라옴."""
    if overlap <= 0 or not tokens:
        return []
    return tokens[-overlap:]

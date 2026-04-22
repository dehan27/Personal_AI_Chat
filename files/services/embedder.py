"""텍스트를 OpenAI 임베딩 API에 보내 벡터로 변환하는 모듈.

- 여러 청크를 한 번에 배치로 보내서 API 호출 수 최소화
- 간단한 재시도(최대 3회) 로직 포함
"""

import os
import time
from typing import List

from openai import OpenAI, APIError


# 임베딩 모델 (DB VectorField의 dimensions와 반드시 일치)
EMBEDDING_MODEL = 'text-embedding-3-small'
EMBEDDING_DIM = 1536

# 한 번의 API 호출에 담을 청크 수 상한
BATCH_SIZE = 100

# 재시도 정책
MAX_RETRIES = 3
RETRY_DELAY_SEC = 1.0


class EmbeddingError(Exception):
    """임베딩 호출 실패."""


def _get_client() -> OpenAI:
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        raise EmbeddingError('OPENAI_API_KEY 환경변수가 설정되지 않았습니다.')
    return OpenAI(api_key=api_key)


def embed_texts(texts: List[str]) -> List[List[float]]:
    """여러 텍스트를 한꺼번에 임베딩한다."""
    if not texts:
        return []

    client = _get_client()
    all_vectors: List[List[float]] = []

    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start:start + BATCH_SIZE]
        vectors = _embed_one_batch(client, batch)
        all_vectors.extend(vectors)

    return all_vectors


def embed_text(text: str) -> List[float]:
    """텍스트 하나를 임베딩."""
    vectors = embed_texts([text])
    return vectors[0]


def _embed_one_batch(client: OpenAI, batch: List[str]) -> List[List[float]]:
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=batch,
            )
            return [item.embedding for item in response.data]
        except (APIError, Exception) as e:
            last_error = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SEC * attempt)
    raise EmbeddingError(f'임베딩 호출 실패 ({MAX_RETRIES}회 재시도 후): {last_error}')

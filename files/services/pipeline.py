"""업로드 파일 처리 파이프라인.

두 단계로 분리되어 있다.
  A) extract_document(doc) — 텍스트만 추출해서 edited_text에 저장, 상태 REVIEWING
  B) finalize_document(doc) — edited_text를 청크·임베딩해서 DB에 저장, 상태 READY

사용자가 review 페이지에서 텍스트를 편집한 뒤 "확정" 버튼을 누르면 B가 실행됨.
"""

import logging

from django.db import transaction

from files.models import Document, DocumentChunk
from files.services.chunker import chunk_text
from files.services.embedder import EmbeddingError, embed_texts
from files.services.extractor import (
    EmptyTextError,
    UnsupportedFileType,
    extract_text,
)


logger = logging.getLogger(__name__)


class PipelineError(Exception):
    """파이프라인 처리 실패."""


# ---------------------------------------------------------------------------
# 단계 A: 파일 → 텍스트 추출
# ---------------------------------------------------------------------------

def extract_document(document: Document) -> None:
    """파일에서 텍스트를 추출해 edited_text에 저장.

    성공 시 status=REVIEWING (사용자 검토 대기)
    실패 시 status=FAILED + error_message 기록
    """
    document.status = Document.Status.PROCESSING
    document.error_message = ''
    document.save(update_fields=['status', 'error_message'])

    try:
        with document.file.open('rb') as fh:
            text = extract_text(fh, document.original_name)
        logger.info('추출 완료: %s (%d자)', document.original_name, len(text))

        document.edited_text = text
        document.status = Document.Status.REVIEWING
        document.error_message = ''
        document.save(update_fields=['edited_text', 'status', 'error_message'])

    except (UnsupportedFileType, EmptyTextError) as e:
        _mark_failed(document, str(e))
        raise PipelineError(str(e)) from e
    except Exception as e:
        logger.exception('추출 예상치 못한 실패: %s', document.original_name)
        _mark_failed(document, f'예상치 못한 오류: {e}')
        raise PipelineError(str(e)) from e


# ---------------------------------------------------------------------------
# 단계 B: edited_text → 청킹 → 임베딩 → 저장
# ---------------------------------------------------------------------------

def finalize_document(document: Document) -> int:
    """edited_text를 청크·임베딩해서 DocumentChunk에 저장.

    Returns: 생성된 청크 수
    """
    text = (document.edited_text or '').strip()
    if not text:
        raise PipelineError('편집된 텍스트가 비어있습니다.')

    document.status = Document.Status.PROCESSING
    document.error_message = ''
    document.save(update_fields=['status', 'error_message'])

    try:
        chunks = chunk_text(text)
        if not chunks:
            raise PipelineError('청크 생성 실패 (텍스트가 비어있습니다)')
        logger.info('청킹: %d개', len(chunks))

        vectors = embed_texts(chunks)
        if len(vectors) != len(chunks):
            raise PipelineError(
                f'임베딩 개수 불일치 (청크 {len(chunks)}개, 벡터 {len(vectors)}개)'
            )
        logger.info('임베딩: %d개', len(vectors))

        with transaction.atomic():
            DocumentChunk.objects.filter(document=document).delete()
            DocumentChunk.objects.bulk_create([
                DocumentChunk(
                    document=document,
                    chunk_index=i,
                    content=chunk,
                    embedding=vec,
                )
                for i, (chunk, vec) in enumerate(zip(chunks, vectors))
            ])
            document.status = Document.Status.READY
            document.error_message = ''
            document.save(update_fields=['status', 'error_message'])

        return len(chunks)

    except (EmbeddingError, PipelineError) as e:
        _mark_failed(document, str(e))
        raise PipelineError(str(e)) from e
    except Exception as e:
        logger.exception('완료 단계 예상치 못한 실패: %s', document.original_name)
        _mark_failed(document, f'예상치 못한 오류: {e}')
        raise PipelineError(str(e)) from e


# ---------------------------------------------------------------------------
# 공용
# ---------------------------------------------------------------------------

def _mark_failed(document: Document, message: str) -> None:
    document.status = Document.Status.FAILED
    document.error_message = message[:2000]
    document.save(update_fields=['status', 'error_message'])

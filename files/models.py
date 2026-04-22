from django.db import models

from pgvector.django import HnswIndex, VectorField

# 임베딩 차원 (text-embedding-3-small 기준)
EMBEDDING_DIM = 1536


class Document(models.Model):
    """업로드된 회사 자료 원본 파일."""

    class Status(models.TextChoices):
        PENDING = 'pending', '대기'
        REVIEWING = 'reviewing', '검토대기'
        PROCESSING = 'processing', '처리중'
        READY = 'ready', '준비완료'
        FAILED = 'failed', '실패'

    file = models.FileField(upload_to='origin/')
    original_name = models.CharField(max_length=255)
    size_bytes = models.BigIntegerField()
    mime_type = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    error_message = models.TextField(blank=True)
    # 추출된 텍스트(사용자 편집 가능). 임베딩 시 이 값을 사용.
    edited_text = models.TextField(blank=True, default='')
    uploaded_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return self.original_name


class DocumentChunk(models.Model):
    """문서를 잘라낸 조각 + 임베딩. RAG 검색 대상."""

    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name='chunks',
    )
    chunk_index = models.PositiveIntegerField()
    content = models.TextField()
    embedding = VectorField(dimensions=EMBEDDING_DIM)
    # 페이지 번호, 헤딩 등 부가정보
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['document_id', 'chunk_index']
        constraints = [
            models.UniqueConstraint(
                fields=['document', 'chunk_index'],
                name='doc_chunk_unique_idx',
            ),
        ]
        indexes = [
            HnswIndex(
                name='doc_chunk_emb_hnsw',
                fields=['embedding'],
                m=16,
                ef_construction=64,
                opclasses=['vector_cosine_ops'],
            ),
        ]

    def __str__(self):
        return f'{self.document_id}#{self.chunk_index}'

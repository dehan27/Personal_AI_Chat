from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    """DocumentChunk.content 에 대한 하이브리드 검색용 인덱스.

    - pg_trgm 확장 활성화 (이미 있으면 무시)
    - GIN 인덱스 생성 (trigram 기반 부분 매칭용)
    """

    dependencies = [
        ('files', '0002_alter_document_file'),
    ]

    operations = [
        TrigramExtension(),
        migrations.RunSQL(
            sql=(
                'CREATE INDEX IF NOT EXISTS doc_chunk_content_trgm_idx '
                'ON files_documentchunk USING gin (content gin_trgm_ops);'
            ),
            reverse_sql=(
                'DROP INDEX IF EXISTS doc_chunk_content_trgm_idx;'
            ),
        ),
    ]

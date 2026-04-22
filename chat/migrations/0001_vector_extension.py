from django.db import migrations

from pgvector.django import VectorExtension


class Migration(migrations.Migration):
    """pgvector 확장을 활성화. 여러 앱에서 vector 타입을 쓰기 전에 단 한 번만 실행되면 됨."""

    initial = True

    dependencies = []

    operations = [
        VectorExtension(),
    ]

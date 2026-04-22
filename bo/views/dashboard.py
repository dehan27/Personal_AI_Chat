from datetime import timedelta

from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import render
from django.utils import timezone

from chat.models import TokenUsage


# 대시보드에서 보여줄 과거 일수
DASHBOARD_DAYS = 7


def dashboard(request):
    # 최근 N일 범위
    now = timezone.localtime()
    since = now - timedelta(days=DASHBOARD_DAYS - 1)
    since_start = since.replace(hour=0, minute=0, second=0, microsecond=0)

    # 일별 집계 쿼리
    daily_rows = (
        TokenUsage.objects
        .filter(created_at__gte=since_start)
        .annotate(date=TruncDate('created_at'))
        .values('date')
        .annotate(
            calls=Count('id'),
            prompt=Sum('prompt_tokens'),
            completion=Sum('completion_tokens'),
            total=Sum('total_tokens'),
        )
        .order_by('-date')
    )

    # 전체 기간 합계 (상단 요약 카드용)
    totals = TokenUsage.objects.filter(created_at__gte=since_start).aggregate(
        calls=Count('id'),
        prompt=Sum('prompt_tokens'),
        completion=Sum('completion_tokens'),
        total=Sum('total_tokens'),
    )

    context = {
        'rows': list(daily_rows),
        'totals': totals,
        'days': DASHBOARD_DAYS,
    }
    return render(request, 'bo/dashboard.html', context)

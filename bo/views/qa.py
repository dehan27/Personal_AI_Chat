"""Q&A 관리 뷰.

세 섹션으로 구성:
- 대화 로그 (ChatLog) — 모든 채팅 기록, "승격" 액션으로 CanonicalQA 생성
- 답변 응답 (ChatLog with Feedback) — 피드백 있는 로그만
- 공식 Q&A (CanonicalQA) — 편집/삭제
"""

from urllib.parse import urlparse

from django.contrib import messages
from django.db.models import Count, F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from chat.models import CanonicalQA, ChatLog, Feedback
from chat.services.qa_retriever import promote_to_canonical
from files.models import Document


PAGE_SIZE = 50


# ---------------------------------------------------------------------------
# 루트 — 대화 로그로 리다이렉트
# ---------------------------------------------------------------------------

def qa_root(request):
    return redirect('bo:qa_logs')


# ---------------------------------------------------------------------------
# 대화 로그 (ChatLog)
# ---------------------------------------------------------------------------

def qa_logs(request):
    tab = request.GET.get('tab', 'pending')

    base_qs = ChatLog.objects.annotate(
        up_count=Count('feedbacks', filter=Q(feedbacks__rating=Feedback.Rating.UP)),
        down_count=Count('feedbacks', filter=Q(feedbacks__rating=Feedback.Rating.DOWN)),
        promoted_count=Count('promotions'),
    )

    if tab == 'pending':
        qs = base_qs.filter(promoted_count=0)
    elif tab == 'promoted':
        qs = base_qs.filter(promoted_count__gt=0)
    else:
        tab = 'all'
        qs = base_qs

    items = list(qs.order_by('-created_at')[:PAGE_SIZE])
    _enrich_with_sources(items)

    # 탭별 카운트
    all_base = ChatLog.objects.annotate(promoted_count=Count('promotions'))
    tab_counts = {
        'pending': all_base.filter(promoted_count=0).count(),
        'promoted': all_base.filter(promoted_count__gt=0).count(),
        'all': ChatLog.objects.count(),
    }

    context = {
        'section': 'logs',
        'items': items,
        'tab': tab,
        'tab_counts': tab_counts,
        'counts': {
            'logs': ChatLog.objects.count(),
            'feedback': Feedback.objects.values('chat_log_id').distinct().count(),
            'canonical': CanonicalQA.objects.count(),
        },
    }
    return render(request, 'bo/qa_logs.html', context)


# ---------------------------------------------------------------------------
# 답변 응답 (피드백 있는 ChatLog)
# ---------------------------------------------------------------------------

def qa_feedback(request):
    tab = request.GET.get('tab', 'all')

    base_qs = (
        ChatLog.objects
        .annotate(
            up_count=Count('feedbacks', filter=Q(feedbacks__rating=Feedback.Rating.UP)),
            down_count=Count('feedbacks', filter=Q(feedbacks__rating=Feedback.Rating.DOWN)),
            promoted_count=Count('promotions'),
        )
    )

    # 다수결 분류: 👎 >= 👍 이고 👎 > 0 → 나쁨 / 👍 > 👎 → 좋음
    if tab == 'down':
        qs = base_qs.filter(down_count__gt=0, down_count__gte=F('up_count')).order_by('-down_count', '-created_at')
    elif tab == 'up':
        qs = base_qs.filter(up_count__gt=F('down_count')).order_by('-up_count', '-created_at')
    else:
        tab = 'all'
        qs = base_qs.annotate(total=Count('feedbacks')).filter(total__gt=0).order_by('-total', '-created_at')

    items = list(qs[:PAGE_SIZE])
    _enrich_with_sources(items)

    # 카운트 — 동일 로직으로 집계
    feedback_base = ChatLog.objects.annotate(
        up=Count('feedbacks', filter=Q(feedbacks__rating=Feedback.Rating.UP)),
        down=Count('feedbacks', filter=Q(feedbacks__rating=Feedback.Rating.DOWN)),
        total=Count('feedbacks'),
    ).filter(total__gt=0)

    counts = {
        'down': feedback_base.filter(down__gt=0, down__gte=F('up')).count(),
        'up': feedback_base.filter(up__gt=F('down')).count(),
        'all': feedback_base.count(),
        'logs': ChatLog.objects.count(),
        'feedback': feedback_base.count(),
        'canonical': CanonicalQA.objects.count(),
    }

    context = {'section': 'feedback', 'items': items, 'tab': tab, 'counts': counts}
    return render(request, 'bo/qa_feedback.html', context)


# ---------------------------------------------------------------------------
# 공식 Q&A (CanonicalQA)
# ---------------------------------------------------------------------------

def qa_canonical(request):
    items = list(CanonicalQA.objects.select_related('source_chatlog').order_by('-created_at')[:PAGE_SIZE])

    referenced_ids = {did for qa in items for did in (qa.sources or [])}
    doc_names = {
        d.pk: d.original_name
        for d in Document.objects.filter(pk__in=referenced_ids)
    }
    for qa in items:
        qa.source_names = [
            doc_names.get(did, f'(삭제됨 #{did})')
            for did in (qa.sources or [])
        ]

    context = {
        'section': 'canonical',
        'items': items,
        'counts': {
            'logs': ChatLog.objects.count(),
            'feedback': Feedback.objects.values('chat_log_id').distinct().count(),
            'canonical': CanonicalQA.objects.count(),
        },
    }
    return render(request, 'bo/qa_canonical.html', context)


# ---------------------------------------------------------------------------
# 액션
# ---------------------------------------------------------------------------

@require_POST
def qa_promote(request, pk):
    """ChatLog → CanonicalQA 승격."""
    cl = get_object_or_404(ChatLog, pk=pk)
    promote_to_canonical(cl)
    messages.success(request, '공식 Q&A로 승격했습니다.')
    return redirect(_back_to(request))


@require_POST
def qa_log_delete(request, pk):
    """ChatLog 삭제 (Feedback CASCADE)."""
    ChatLog.objects.filter(pk=pk).delete()
    messages.success(request, '대화 로그를 삭제했습니다.')
    return redirect(_back_to(request))


@require_POST
def qa_canonical_update(request, pk):
    """CanonicalQA 답변 편집."""
    qa = get_object_or_404(CanonicalQA, pk=pk)
    new_question = (request.POST.get('question') or '').strip()
    new_answer = (request.POST.get('answer') or '').strip()
    if not new_question or not new_answer:
        messages.error(request, '질문/답변이 비어있습니다.')
        return redirect(_back_to(request))

    from files.services.embedder import embed_text
    qa.question = new_question
    qa.answer = new_answer
    # 질문이 바뀌면 임베딩도 다시 계산
    qa.question_embedding = embed_text(new_question)
    qa.save(update_fields=['question', 'answer', 'question_embedding'])
    messages.success(request, '공식 Q&A가 수정되었습니다.')
    return redirect(_back_to(request))


@require_POST
def qa_canonical_delete(request, pk):
    """CanonicalQA 삭제."""
    CanonicalQA.objects.filter(pk=pk).delete()
    messages.success(request, '공식 Q&A를 삭제했습니다.')
    return redirect(_back_to(request))


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _enrich_with_sources(items):
    referenced_ids = {did for qa in items for did in (qa.sources or [])}
    doc_names = {
        d.pk: d.original_name
        for d in Document.objects.filter(pk__in=referenced_ids)
    }
    for qa in items:
        qa.source_names = [
            doc_names.get(did, f'(삭제됨 #{did})')
            for did in (qa.sources or [])
        ]


def _back_to(request) -> str:
    ref = request.META.get('HTTP_REFERER', '')
    if ref:
        p = urlparse(ref)
        if p.path.startswith('/bo/qa/'):
            return ref
    return '/bo/qa/logs/'

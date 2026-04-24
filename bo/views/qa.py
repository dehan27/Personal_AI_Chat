"""Q&A 관리 뷰.

세 섹션으로 구성:
- 대화 로그 (ChatLog) — 모든 채팅 기록, "승격" 액션으로 CanonicalQA 생성
- 답변 응답 (ChatLog with Feedback) — 피드백 있는 로그만
- 공식 Q&A (CanonicalQA) — 편집/삭제
"""

from urllib.parse import urlparse

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from chat.models import CanonicalQA, ChatLog, Feedback
from chat.services.qa_retriever import promote_to_canonical
from files.models import Document


# 페이지당 항목 수. 대화 로그·피드백·공식 Q&A 세 섹션 공통.
# files 관리(20) 보다 작은 이유: Q&A 카드 한 장의 높이(질문+답변+메타+액션)가
# 파일 행보다 훨씬 커서 한 화면에 너무 많이 깔리면 스크롤 피로가 큼.
PAGE_SIZE = 10


# ---------------------------------------------------------------------------
# 루트 — 대화 로그로 리다이렉트
# ---------------------------------------------------------------------------

def qa_root(request):
    return redirect('bo:qa_logs')


# ---------------------------------------------------------------------------
# 대화 로그 (ChatLog)
# ---------------------------------------------------------------------------

def qa_logs(request):
    tab = request.GET.get('tab', 'all')
    q = (request.GET.get('q') or '').strip()

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

    if q:
        qs = qs.filter(question__icontains=q)

    paginator = Paginator(qs.order_by('-created_at'), PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))
    items = list(page_obj.object_list)
    _enrich_with_sources(items)

    # 탭별 카운트 — 검색어는 적용하지 않는다(탭 배지는 전체 규모를 보여줘야 함)
    all_base = ChatLog.objects.annotate(promoted_count=Count('promotions'))
    tab_counts = {
        'pending': all_base.filter(promoted_count=0).count(),
        'promoted': all_base.filter(promoted_count__gt=0).count(),
        'all': ChatLog.objects.count(),
    }

    context = {
        'section': 'logs',
        'items': items,
        'page_obj': page_obj,
        'total_count': paginator.count,
        'tab': tab,
        'q': q,
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
    q = (request.GET.get('q') or '').strip()

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

    if q:
        qs = qs.filter(question__icontains=q)

    paginator = Paginator(qs, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))
    items = list(page_obj.object_list)
    _enrich_with_sources(items)

    # 카운트 — 동일 로직으로 집계 (검색어 적용 없음)
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

    context = {
        'section': 'feedback',
        'items': items,
        'page_obj': page_obj,
        'total_count': paginator.count,
        'tab': tab,
        'q': q,
        'counts': counts,
    }
    return render(request, 'bo/qa_feedback.html', context)


# ---------------------------------------------------------------------------
# 공식 Q&A (CanonicalQA)
# ---------------------------------------------------------------------------

def qa_canonical(request):
    q = (request.GET.get('q') or '').strip()

    base_qs = CanonicalQA.objects.select_related('source_chatlog').order_by('-created_at')
    if q:
        base_qs = base_qs.filter(question__icontains=q)

    paginator = Paginator(base_qs, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))
    items = list(page_obj.object_list)

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
        'page_obj': page_obj,
        'total_count': paginator.count,
        'q': q,
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
    """ChatLog 삭제 (Feedback CASCADE).

    공식 Q&A로 승격된 로그는 여기서 삭제 불가(공식 Q&A 창에서만 관리).
    """
    cl = get_object_or_404(
        ChatLog.objects.annotate(promoted_count=Count('promotions')),
        pk=pk,
    )
    if cl.promoted_count > 0:
        messages.error(request, '공식 Q&A로 승격된 로그는 공식 Q&A 창에서만 삭제할 수 있습니다.')
        return redirect(_back_to(request))
    cl.delete()
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
# 일괄 액션 (Bulk)
# ---------------------------------------------------------------------------

@require_POST
def qa_bulk_promote(request):
    """선택된 ChatLog 들을 한 번에 공식 Q&A 로 승격.

    이미 승격된 로그는 자동 제외(단건 `qa_promote` 와 동일한 규칙을 유지).
    """
    ids = _parsed_ids(request)
    if not ids:
        messages.error(request, '선택된 항목이 없습니다.')
        return redirect(_back_to(request))

    targets = (
        ChatLog.objects
        .annotate(promoted_count=Count('promotions'))
        .filter(pk__in=ids, promoted_count=0)
    )
    promoted = 0
    for cl in targets:
        promote_to_canonical(cl)
        promoted += 1

    skipped = len(ids) - promoted
    if promoted and skipped:
        messages.success(request, f'{promoted}건 승격, {skipped}건은 이미 승격돼 건너뜀.')
    elif promoted:
        messages.success(request, f'{promoted}건을 공식 Q&A 로 승격했습니다.')
    else:
        messages.error(request, '이미 모두 승격된 로그입니다.')
    return redirect(_back_to(request))


@require_POST
def qa_bulk_delete_logs(request):
    """선택된 ChatLog 일괄 삭제.

    승격된 로그는 단건 규칙대로 이 경로에서 삭제 불가(공식 Q&A 창에서만).
    """
    ids = _parsed_ids(request)
    if not ids:
        messages.error(request, '선택된 항목이 없습니다.')
        return redirect(_back_to(request))

    # 삭제 제외 대상(이미 승격된 로그) pk 집합을 먼저 추린다.
    promoted_ids = set(
        ChatLog.objects
        .annotate(promoted_count=Count('promotions'))
        .filter(pk__in=ids, promoted_count__gt=0)
        .values_list('pk', flat=True)
    )
    deletable_ids = [i for i in ids if i not in promoted_ids]
    ChatLog.objects.filter(pk__in=deletable_ids).delete()

    deleted = len(deletable_ids)
    skipped = len(promoted_ids)
    if deleted and skipped:
        messages.success(request, f'{deleted}건 삭제, {skipped}건은 승격됨 상태라 건너뜀.')
    elif deleted:
        messages.success(request, f'{deleted}건을 삭제했습니다.')
    else:
        messages.error(request, '선택한 로그 모두 승격됨 상태라 삭제할 수 없습니다.')
    return redirect(_back_to(request))


@require_POST
def qa_bulk_delete_canonical(request):
    """선택된 공식 Q&A 일괄 삭제."""
    ids = _parsed_ids(request)
    if not ids:
        messages.error(request, '선택된 항목이 없습니다.')
        return redirect(_back_to(request))

    deleted = CanonicalQA.objects.filter(pk__in=ids).count()
    CanonicalQA.objects.filter(pk__in=ids).delete()
    messages.success(request, f'{deleted}건을 삭제했습니다.')
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


def _parsed_ids(request) -> list[int]:
    """POST body 의 `ids` 목록을 정수 리스트로. 숫자 아닌 값은 버린다."""
    result: list[int] = []
    for raw in request.POST.getlist('ids'):
        try:
            result.append(int(raw))
        except (TypeError, ValueError):
            continue
    # 중복 제거 + 순서 보존
    seen: set[int] = set()
    unique: list[int] = []
    for i in result:
        if i not in seen:
            seen.add(i)
            unique.append(i)
    return unique

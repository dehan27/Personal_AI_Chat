import mimetypes
from pathlib import Path

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from chat.models import CanonicalQA, ChatLog
from files.models import Document
from files.services.chunker import count_tokens
from files.services.embedder import EMBEDDING_MODEL
from files.services.pipeline import (
    PipelineError,
    extract_document,
    finalize_document,
)


# 업로드 설정
ALLOWED_EXTS = {'.txt', '.md', '.pdf', '.docx'}
MAX_SIZE_BYTES = 20 * 1024 * 1024  # 20MB

# 파일 목록 페이지당 표시 수
FILES_PER_PAGE = 5

# 청킹 기준 (chunker.DEFAULT_CHUNK_TOKENS / DEFAULT_OVERLAP_TOKENS 일치)
CHUNK_TOKENS = 500
CHUNK_OVERLAP_TOKENS = 100

# 비용 추정 (text-embedding-3-small: $0.02 per 1M tokens)
EMBED_PRICE_USD_PER_1M = 0.02
USD_TO_KRW = 1350


def files(request):
    paginator = Paginator(Document.objects.all(), FILES_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get('page'))
    context = {
        'documents': page_obj,       # 템플릿에서 for 순회 시 현재 페이지 항목
        'page_obj': page_obj,         # 페이지네이션 컨트롤용
        'total_count': paginator.count,
        'allowed_exts': ', '.join(sorted(ALLOWED_EXTS)),
        'max_size_mb': MAX_SIZE_BYTES // (1024 * 1024),
    }
    return render(request, 'bo/files.html', context)


@require_POST
def upload(request):
    f = request.FILES.get('file')
    if not f:
        messages.error(request, '파일을 선택해주세요.')
        return redirect('bo:files')

    if f.size > MAX_SIZE_BYTES:
        messages.error(request, f'파일 크기가 {MAX_SIZE_BYTES // (1024*1024)}MB를 초과합니다.')
        return redirect('bo:files')

    ext = Path(f.name).suffix.lower()
    if ext not in ALLOWED_EXTS:
        messages.error(request, f'허용되지 않은 확장자입니다: {ext or "(없음)"}')
        return redirect('bo:files')

    # 1) Document 생성 + 파일 저장 (상태 PENDING)
    mime_type = f.content_type or mimetypes.guess_type(f.name)[0] or ''
    doc = Document.objects.create(
        file=f,
        original_name=f.name,
        size_bytes=f.size,
        mime_type=mime_type,
        status=Document.Status.PENDING,
    )

    # 2) 텍스트 추출만 실행 → 상태 REVIEWING
    try:
        extract_document(doc)
    except PipelineError as e:
        messages.error(request, f'"{f.name}" 텍스트 추출 실패: {e}')
        return redirect('bo:files')

    # 3) 검토 페이지로 이동
    return redirect('bo:review', pk=doc.pk)


def review(request, pk):
    """미리보기·편집 페이지."""
    doc = get_object_or_404(Document, pk=pk)

    text = doc.edited_text or ''
    char_count = len(text)
    token_count = count_tokens(text) if text else 0

    # 예상 청크 수 (오버랩 감안 대략치)
    if token_count == 0:
        chunk_estimate = 0
    else:
        chunk_estimate = max(1, (token_count + (CHUNK_TOKENS - CHUNK_OVERLAP_TOKENS) - 1) // (CHUNK_TOKENS - CHUNK_OVERLAP_TOKENS))

    # 비용 추정 (원화)
    cost_krw = (token_count / 1_000_000) * EMBED_PRICE_USD_PER_1M * USD_TO_KRW

    # 경고 수집
    warnings = []
    if char_count == 0:
        warnings.append('추출된 텍스트가 비어있습니다. 원본 파일에 텍스트가 없거나 추출이 실패했을 수 있습니다.')
    elif char_count < 50:
        warnings.append('추출된 텍스트가 매우 짧습니다 (50자 미만). 스캔 PDF일 가능성이 있습니다.')
    elif doc.original_name.lower().endswith('.pdf'):
        # 페이지 수 기반 스캔본 의심 체크는 별도 라이브러리 접근 필요. 여기선 단순 기준만.
        pass

    context = {
        'doc': doc,
        'text': text,
        'char_count': char_count,
        'token_count': token_count,
        'chunk_estimate': chunk_estimate,
        'cost_krw': cost_krw,
        'warnings': warnings,
        'embed_model': EMBEDDING_MODEL,
    }
    return render(request, 'bo/files_review.html', context)


@require_POST
def confirm(request, pk):
    """사용자가 편집한 텍스트로 임베딩을 확정 실행."""
    doc = get_object_or_404(Document, pk=pk)

    edited = (request.POST.get('edited_text') or '').strip()
    if not edited:
        messages.error(request, '편집된 텍스트가 비어있습니다.')
        return redirect('bo:review', pk=pk)

    # 편집본 저장
    doc.edited_text = edited
    doc.save(update_fields=['edited_text'])

    # 임베딩 파이프라인 실행
    try:
        chunk_count = finalize_document(doc)
    except PipelineError as e:
        messages.error(request, f'임베딩 실패: {e}')
        return redirect('bo:review', pk=pk)

    messages.success(request, f'"{doc.original_name}" 처리 완료 (청크 {chunk_count}개)')
    return redirect('bo:files')


@require_POST
def delete(request, pk):
    doc = get_object_or_404(Document, pk=pk)
    name = doc.original_name

    # 이 문서를 근거로 만들어진 ChatLog / CanonicalQA 삭제 (고아 답변 방지)
    cl_deleted, _ = ChatLog.objects.filter(sources__contains=[doc.pk]).delete()
    canonical_deleted, _ = CanonicalQA.objects.filter(sources__contains=[doc.pk]).delete()

    # DocumentChunk는 FK CASCADE로 자동 삭제됨
    doc.file.delete(save=False)
    doc.delete()

    msg = f'"{name}" 삭제됨'
    if cl_deleted or canonical_deleted:
        msg += f' (관련 ChatLog {cl_deleted}건, 공식 Q&A {canonical_deleted}건도 삭제)'
    messages.success(request, msg)
    return redirect('bo:files')

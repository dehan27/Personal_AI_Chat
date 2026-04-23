"""Prompt 관리 뷰.

allow-list(chat.services.prompt_registry) 기반으로 등록된 프롬프트만 노출·편집.
URL 의 key 가 registry 에 없으면 404, editable=False 면 403.
저장 경로는 서버 코드(registry.relative_path)에서만 결정되고, 사용자 입력의
경로는 절대 받지 않는다.
"""

from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from chat.services.prompt_loader import PromptNotFound, load_prompt, save_prompt
from chat.services.prompt_registry import all_entries, get_entry


def prompts_index(request):
    """편집 가능한 프롬프트 목록."""
    entries = all_entries()
    context = {
        'section': 'prompts',
        'entries': entries,
    }
    return render(request, 'bo/prompts.html', context)


def prompts_edit(request, key: str):
    """프롬프트 편집 페이지 (GET)."""
    entry = get_entry(key)
    if entry is None:
        # 등록되지 않은 key → 404 처럼 취급하되 메시지와 함께 목록으로 돌려보냄
        messages.error(request, '등록되지 않은 프롬프트입니다.')
        return redirect('bo:prompts')

    try:
        content = load_prompt(entry.relative_path)
    except PromptNotFound:
        content = ''
        messages.warning(
            request,
            f'프롬프트 파일이 존재하지 않습니다: {entry.relative_path}. 저장하면 새로 생성됩니다.',
        )

    context = {
        'section': 'prompts',
        'entry': entry,
        'content': content,
    }
    return render(request, 'bo/prompts_edit.html', context)


@require_POST
def prompts_update(request, key: str):
    """프롬프트 저장."""
    entry = get_entry(key)
    if entry is None:
        messages.error(request, '등록되지 않은 프롬프트입니다.')
        return redirect('bo:prompts')

    if not entry.editable:
        return HttpResponseForbidden('이 프롬프트는 편집이 허용되지 않습니다.')

    content = request.POST.get('content', '')
    # 완전 공백만 있는 저장은 차단 (실수로 비우는 사고 방지)
    if not content.strip():
        messages.error(request, '빈 프롬프트는 저장할 수 없습니다.')
        return redirect('bo:prompts_edit', key=key)

    # 줄바꿈 표준화: 사용자가 붙여넣은 \r\n 을 \n 으로
    normalized = content.replace('\r\n', '\n').replace('\r', '\n')

    save_prompt(entry.relative_path, normalized)
    messages.success(request, f'프롬프트 "{entry.title}" 를 저장했습니다.')
    return redirect('bo:prompts_edit', key=key)

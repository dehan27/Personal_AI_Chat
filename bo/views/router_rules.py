"""Router Rule 관리 뷰 (Phase 4-2).

`RouterRule` CRUD 를 BO 에 노출. Phase 4-2 범위는 contains 매칭뿐이고 preview /
conflict detection / 변경 이력은 다루지 않는다 (설계 §5-6 out-of-scope).

저장 흐름:
    1. ModelForm 으로 입력 수신
    2. 유효하면 save + success 메시지 → 목록으로 redirect
    3. 잘못된 pk 는 404 (or 에러 메시지 + 목록 redirect)
"""

from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from chat.models import RouterRule
from chat.services.question_router import AGENT_KEYWORDS, WORKFLOW_KEYWORDS


class RouterRuleForm(forms.ModelForm):
    """새 rule 생성 / 기존 rule 편집 공용 폼.

    모든 위젯에 bo.css 의 `.input` 클래스를 주입해 디자인 가이드 Form 규격을
    그대로 적용한다 (guide §Form / bo.css FORM 섹션).
    """

    class Meta:
        model = RouterRule
        fields = (
            'name',
            'route',
            'match_type',
            'pattern',
            'priority',
            'enabled',
            'description',
        )
        widgets = {
            'name':        forms.TextInput(attrs={'class': 'input'}),
            'route':       forms.Select(attrs={'class': 'input'}),
            'match_type':  forms.Select(attrs={'class': 'input'}),
            'pattern':     forms.TextInput(attrs={'class': 'input'}),
            'priority':    forms.NumberInput(attrs={'class': 'input'}),
            'description': forms.Textarea(attrs={'class': 'input', 'rows': 3}),
            # enabled 는 체크박스 — `.input` 을 적용하면 스타일이 깨진다.
        }


def router_rules_index(request):
    """RouterRule 목록 + 코드 내장 기본 키워드(읽기 전용).

    기본 키워드는 question_router 의 fallback 레이어 — DB rule 이 매치되지
    않을 때 실제 분류를 담당. 운영자가 'BO 가 비어도 기본 동작은 무엇인가' 를
    확인할 수 있도록 접이식 섹션으로 함께 노출한다. 수정은 코드에서만 가능.
    """
    context = {
        'section': 'router_rules',
        'rules': RouterRule.objects.all(),
        'workflow_keywords': WORKFLOW_KEYWORDS,
        'agent_keywords': AGENT_KEYWORDS,
    }
    return render(request, 'bo/router_rules.html', context)


def router_rules_new(request):
    """신규 rule 생성."""
    if request.method == 'POST':
        form = RouterRuleForm(request.POST)
        if form.is_valid():
            rule = form.save()
            messages.success(request, f'규칙 "{rule.name}" 를 추가했습니다.')
            return redirect('bo:router_rules')
    else:
        form = RouterRuleForm()

    context = {
        'section': 'router_rules',
        'form': form,
        'mode': 'new',
    }
    return render(request, 'bo/router_rule_form.html', context)


def router_rules_edit(request, pk: int):
    """기존 rule 편집."""
    rule = get_object_or_404(RouterRule, pk=pk)
    if request.method == 'POST':
        form = RouterRuleForm(request.POST, instance=rule)
        if form.is_valid():
            form.save()
            messages.success(request, f'규칙 "{rule.name}" 를 저장했습니다.')
            return redirect('bo:router_rules')
    else:
        form = RouterRuleForm(instance=rule)

    context = {
        'section': 'router_rules',
        'form': form,
        'mode': 'edit',
        'rule': rule,
    }
    return render(request, 'bo/router_rule_form.html', context)


@require_POST
def router_rules_toggle(request, pk: int):
    """enabled 토글 — rule 을 삭제 없이 즉시 무력화/재활성화."""
    rule = get_object_or_404(RouterRule, pk=pk)
    rule.enabled = not rule.enabled
    rule.save(update_fields=['enabled', 'updated_at'])
    state = '활성화' if rule.enabled else '비활성화'
    messages.success(request, f'규칙 "{rule.name}" 를 {state}했습니다.')
    return redirect('bo:router_rules')


@require_POST
def router_rules_delete(request, pk: int):
    """rule 완전 삭제. 기본 동작은 코드 상수가 담당하므로 복구 불필요."""
    rule = get_object_or_404(RouterRule, pk=pk)
    name = rule.name
    rule.delete()
    messages.success(request, f'규칙 "{name}" 를 삭제했습니다.')
    return redirect('bo:router_rules')

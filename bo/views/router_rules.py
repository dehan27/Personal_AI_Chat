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


class RouterRuleForm(forms.ModelForm):
    """새 rule 생성 / 기존 rule 편집 공용 폼."""

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
            'description': forms.Textarea(attrs={'rows': 3}),
        }


def router_rules_index(request):
    """RouterRule 목록 — priority DESC (모델 Meta.ordering 이 담당)."""
    context = {
        'section': 'router_rules',
        'rules': RouterRule.objects.all(),
    }
    return render(request, 'bo/router_rules.html', context)


def router_rules_new(request):
    """신규 rule 생성."""
    if request.method == 'POST':
        form = RouterRuleForm(request.POST)
        if form.is_valid():
            rule = form.save()
            messages.success(request, f'Rule "{rule.name}" 를 추가했습니다.')
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
            messages.success(request, f'Rule "{rule.name}" 를 저장했습니다.')
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
    messages.success(request, f'Rule "{rule.name}" 를 {state}했습니다.')
    return redirect('bo:router_rules')


@require_POST
def router_rules_delete(request, pk: int):
    """rule 완전 삭제. 기본 동작은 코드 상수가 담당하므로 복구 불필요."""
    rule = get_object_or_404(RouterRule, pk=pk)
    name = rule.name
    rule.delete()
    messages.success(request, f'Rule "{name}" 를 삭제했습니다.')
    return redirect('bo:router_rules')

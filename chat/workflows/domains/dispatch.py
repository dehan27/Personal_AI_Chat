"""workflow_key → 실행 디스패처 (Phase 6-1).

graph 의 `workflow_node` 가 유일한 호출자. registry 에 등록된 key 면 Phase 5
`run_workflow(...)` 로 4 단계 계약을 돌리고 `WorkflowResult` 를 반환, 미등록
key 는 `WorkflowResult.unsupported(...)` 로 번역한다.

여기서는 도메인 로직을 **절대** 두지 않는다. key 해석 + 팩토리 호출만.
"""

from __future__ import annotations

from typing import Any, Mapping

from chat.workflows.core import WorkflowResult, run_workflow
from chat.workflows.domains import registry


def run(workflow_key: str, raw: Mapping[str, Any]) -> WorkflowResult:
    """`workflow_key` 에 해당하는 workflow 를 실행해 결과 반환.

    - 등록된 key → `run_workflow(workflow, raw)` 결과 그대로.
    - 빈 key / 미등록 key → `WorkflowResult.unsupported(...)`.
    """
    key = (workflow_key or '').strip()
    if not key:
        return WorkflowResult.unsupported('workflow_key 가 지정되지 않았습니다.')

    entry = registry.get(key)
    if entry is None:
        return WorkflowResult.unsupported(
            f'등록되지 않은 workflow_key 입니다: {key!r}'
        )

    workflow = entry.factory()
    return run_workflow(workflow, raw)

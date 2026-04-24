"""Workflow domains (Phase 6).

Phase 5 `core/` 가 범용 유틸·타입이라면, `domains/` 는 그 위에서 실제 질문
유형을 처리하는 workflow 들이 사는 곳이다. Phase 6-1 은 infrastructure 와
첫 generic workflow(`date_calculation`) 을 올린다.

구조:
- `registry.py` — `WorkflowEntry` + `register/get/has/all_entries`.
- `dispatch.py` — `run(workflow_key, raw)` 진입점.
- `general/`   — 질문 유형별 generic workflow 구현.

`general` 을 마지막에 import 해서 각 workflow 모듈이 자신을 registry 에
등록하게 한다. 이 파일이 import 되는 시점이 곧 registry 가 채워지는 시점.
"""

from chat.workflows.domains import registry
from chat.workflows.domains.dispatch import run
from chat.workflows.domains.field_spec import FieldSpec
from chat.workflows.domains import general  # noqa: F401  (register 부작용)


__all__ = [
    'registry',
    'run',
    'FieldSpec',
]

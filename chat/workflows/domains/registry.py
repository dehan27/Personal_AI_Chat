"""Workflow key → 실행 엔트리 레지스트리 (Phase 6-1).

Phase 5 core 위에 올라가는 domain workflow 들은 모두 여기에 등록된다.
`dispatch.run(key, raw)` 는 registry 를 조회해 해당 workflow 를 만들고 `Phase 5`
의 `run_workflow(...)` 로 4 단계 계약을 돌린다.

설계 결정:
- **key 는 plain string**. enum 으로 강제하지 않아 BO 드롭다운이 등록된 key 를
  나열하는 구조로도, Phase 6-2/6-3 이 새 key 를 추가하는 구조로도 자연스럽다.
- 각 엔트리는 `factory: () -> BaseWorkflow` 를 갖는다. workflow 는 상태를 들고
  있지 않아야 하지만(Phase 5 §5-2 순수성 원칙), 안전상 호출마다 새 인스턴스를
  만들어 dispatch 와 도메인 구현의 결합도를 낮춘다.
- 파일·모듈 위치: `chat/workflows/domains/registry.py`. 실제 workflow 구현은
  `chat/workflows/domains/general/` 아래. `general/__init__.py` 가 각 모듈의
  `register()` 호출을 모아주기 때문에, `registry.py` 자신은 domain 모듈을
  import 하지 않아 **순환 import 차단**.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from chat.workflows.core import BaseWorkflow


# 상태 리터럴. BO 에서 뱃지 색을 가르기 위해 문자열로 둔다.
STATUS_STABLE = 'stable'
STATUS_BETA = 'beta'


@dataclass(frozen=True)
class WorkflowEntry:
    """등록된 workflow 한 개의 메타 + 팩토리."""
    key: str
    title: str
    description: str
    status: str                          # 'stable' / 'beta'
    factory: Callable[[], BaseWorkflow]


# 실제 저장소 — 모듈 단위 싱글톤.
_REGISTRY: dict[str, WorkflowEntry] = {}


def register(entry: WorkflowEntry) -> None:
    """엔트리를 등록. 같은 key 를 두 번 등록하면 `ValueError`."""
    if not entry.key:
        raise ValueError('register: WorkflowEntry.key 가 비어 있습니다.')
    if entry.key in _REGISTRY:
        raise ValueError(
            f'register: workflow_key={entry.key!r} 가 이미 등록되어 있습니다.'
        )
    _REGISTRY[entry.key] = entry


def get(key: str) -> WorkflowEntry | None:
    """등록된 엔트리 조회. 없으면 `None`."""
    return _REGISTRY.get(key)


def has(key: str) -> bool:
    """등록 여부만 필요할 때."""
    return key in _REGISTRY


def all_entries() -> Iterable[WorkflowEntry]:
    """BO 드롭다운·admin 에서 순회용. 등록 순서 보존."""
    return tuple(_REGISTRY.values())


def _reset_for_tests() -> None:
    """테스트 전용. 프로덕션 코드에서 호출 금지."""
    _REGISTRY.clear()

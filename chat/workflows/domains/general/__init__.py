"""Generic (질문 유형 중심) workflow 도메인들.

각 모듈이 자신을 `chat.workflows.domains.registry` 에 register 하는 부작용을
갖는다. 이 패키지가 import 되는 시점에 전 workflow 가 등록된 상태가 된다.

Phase 6-1 범위는 이 패키지 뼈대만 — 실제 generic workflow 는 이어지는 커밋에서
추가된다.
"""

__all__: list[str] = []

"""Workflow 공용 반환 타입 (Phase 5; Phase 8-1 BaseResult 추출).

이 모듈은 **타입만** 정의한다. 다른 core 모듈을 import 하지 않는다
(순환 방지 · 의존 방향 통제).

세 가지 타입이 있다:

- `ValidationResult` — 입력 정규화·검증 단계의 결과. workflow 내부에서만 쓰이고
  사용자에게 직접 노출되지 않는다.
- `BaseResult` (Phase 8-1) — 도메인 결과 (`WorkflowResult`, `AgentResult` 등) 가
  공유하는 Protocol. 공통 필드 (`status`, `value`, `details`) 만 명시. structural
  typing 으로 implement — 새 도메인 결과를 만들 때 inheritance 강제 안 함.
- `WorkflowResult` — 도메인 workflow 가 최종적으로 돌려주는 결과. response
  layer 가 이것을 사용자 문자열로 변환한다. `BaseResult` 를 implement 하지만
  public 시그니처 / 팩토리 / 필드 변경 없음 (8-1 회귀 0 우선).

설계 원칙 (Phase 5 §2):
- 불변(`frozen=True`) → 합성·로그 안전
- 기본값은 빈 튜플·빈 dict → 동일 인스턴스 공유로 메모리 절약
- `status` 는 문자열 enum → JSON 직렬화·DB 저장·로그 필터 모두 자연스러움
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Tuple, runtime_checkable


_EMPTY_DETAILS: Mapping[str, Any] = MappingProxyType({})


# ---------------------------------------------------------------------------
# ValidationResult — 입력 단계 결과
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ValidationResult:
    """workflow 입력이 정상인지 알리는 결과.

    - `ok=True` 면 나머지 필드는 빈 상태여야 한다(문서적 계약, 런타임 강제는 안 함).
    - `ok=False` 면 `missing_fields` 또는 `errors` 가 최소 한 쪽 이상 채워진다.
    - `missing_fields` 와 `errors` 는 tuple — 불변·해시 가능.
    """

    ok: bool
    missing_fields: Tuple[str, ...] = ()
    errors: Tuple[str, ...] = ()

    # 팩토리 ----------------------------------------------------------

    @classmethod
    def success(cls) -> 'ValidationResult':
        """모든 검증을 통과한 결과."""
        return cls(ok=True)

    @classmethod
    def fail(
        cls,
        *,
        missing: Tuple[str, ...] | list[str] = (),
        errors: Tuple[str, ...] | list[str] = (),
    ) -> 'ValidationResult':
        """검증 실패를 표현. 둘 다 비어 있으면 사용 측 실수로 보고 `ValueError`."""
        missing_tuple = tuple(missing)
        errors_tuple = tuple(errors)
        if not missing_tuple and not errors_tuple:
            raise ValueError(
                'ValidationResult.fail 은 missing 또는 errors 중 하나 이상이 필요합니다.'
            )
        return cls(ok=False, missing_fields=missing_tuple, errors=errors_tuple)


# ---------------------------------------------------------------------------
# WorkflowStatus + WorkflowResult — workflow 최종 반환형
# ---------------------------------------------------------------------------

class WorkflowStatus(str, Enum):
    """domain workflow 의 최종 상태.

    문자열 상속이라 JSON / 로그 / DB 어디에 실어도 값(`"ok"` 등)이 그대로 쓰인다.

    Phase 6-3 에서 `NOT_FOUND` / `UPSTREAM_ERROR` 가 추가되면서 `UNSUPPORTED` 는
    "이 workflow 범위 밖(미등록 key 등)" 의 원 의미로 환원됐다. "자료 없음" 은
    `NOT_FOUND`, "일시 장애" 는 `UPSTREAM_ERROR` 로 분리해 reply·관측·향후
    재시도 정책을 서로 다르게 걸 수 있게 한다.
    """

    OK = 'ok'
    MISSING_INPUT = 'missing_input'
    INVALID_INPUT = 'invalid_input'
    UNSUPPORTED = 'unsupported'
    NOT_FOUND = 'not_found'
    UPSTREAM_ERROR = 'upstream_error'


# ---------------------------------------------------------------------------
# BaseResult — 도메인 결과 공통 Protocol (Phase 8-1)
# ---------------------------------------------------------------------------

@runtime_checkable
class BaseResult(Protocol):
    """도메인 결과 (`WorkflowResult`, `AgentResult` 등) 가 공유하는 구조.

    structural typing — 명시적 inheritance 없이 같은 필드를 가지면 implement 로
    인정. 도메인 결과를 새로 만들 때 강제 base class 가 없어 가벼움.

    공통 필드:
    - `status: WorkflowStatus` — 결과 상태 (OK / NOT_FOUND / UPSTREAM_ERROR 등).
    - `value: Any` — 핵심 계산값. 실패 상태에선 보통 None.
    - `details: Mapping[str, Any]` — 중간 근거 / 메타 (read-only dict).

    reply layer 는 이 Protocol 을 import 하지 않고 각 도메인의 reply 모듈이
    구체 타입 (`WorkflowResult` / `AgentResult`) 을 직접 받는다 — Protocol 은
    어휘 공유 / 후속 확장 용도 (Phase 8-1 plan §1 참고).
    """

    status: WorkflowStatus
    value: Any
    details: Mapping[str, Any]


@dataclass(frozen=True)
class WorkflowResult:
    """workflow 한 번 실행의 최종 결과.

    필드:
    - `status`         — `WorkflowStatus` enum 값
    - `value`          — 핵심 계산값(도메인별). 실패 상태에선 보통 `None`
    - `details`        — 중간 계산 근거나 원자료. read-only dict 로 고정
    - `missing_fields` — 빠진 입력값 이름 (사용자에게 '무엇이 필요한지' 안내용)
    - `warnings`       — 치명적이지 않은 경고. 예: 자료 업데이트 지연
    """

    status: WorkflowStatus
    value: Any = None
    details: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_DETAILS)
    missing_fields: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()

    # 팩토리 ----------------------------------------------------------

    @classmethod
    def ok(
        cls,
        value: Any,
        *,
        details: Mapping[str, Any] | None = None,
        warnings: Tuple[str, ...] | list[str] = (),
    ) -> 'WorkflowResult':
        return cls(
            status=WorkflowStatus.OK,
            value=value,
            details=MappingProxyType(dict(details)) if details else _EMPTY_DETAILS,
            warnings=tuple(warnings),
        )

    @classmethod
    def missing_input(
        cls,
        missing_fields: Tuple[str, ...] | list[str],
        *,
        warnings: Tuple[str, ...] | list[str] = (),
    ) -> 'WorkflowResult':
        return cls(
            status=WorkflowStatus.MISSING_INPUT,
            missing_fields=tuple(missing_fields),
            warnings=tuple(warnings),
        )

    @classmethod
    def invalid_input(
        cls,
        errors: Tuple[str, ...] | list[str],
        *,
        missing_fields: Tuple[str, ...] | list[str] = (),
    ) -> 'WorkflowResult':
        # errors 는 details 에 담아 response layer 가 그대로 문자열로 쓸 수 있게 한다.
        details = MappingProxyType({'errors': tuple(errors)})
        return cls(
            status=WorkflowStatus.INVALID_INPUT,
            details=details,
            missing_fields=tuple(missing_fields),
        )

    @classmethod
    def unsupported(cls, reason: str) -> 'WorkflowResult':
        return cls(
            status=WorkflowStatus.UNSUPPORTED,
            details=MappingProxyType({'reason': reason}),
        )

    @classmethod
    def not_found(cls, reason: str) -> 'WorkflowResult':
        """workflow 가 정상 실행됐지만 질문에 맞는 데이터를 못 찾음."""
        return cls(
            status=WorkflowStatus.NOT_FOUND,
            details=MappingProxyType({'reason': reason}),
        )

    @classmethod
    def upstream_error(cls, reason: str) -> 'WorkflowResult':
        """LLM / 네트워크 / 파서 등 일시적 실행 실패 — 재시도 대상."""
        return cls(
            status=WorkflowStatus.UPSTREAM_ERROR,
            details=MappingProxyType({'reason': reason}),
        )

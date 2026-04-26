"""Agent Tool 데이터타입 + 모듈 레지스트리 (Phase 7-1).

`Tool` 의 핵심 결정:

- `input_schema: Optional[Mapping[str, FieldSpec]]`
  - **schema 모드**: Phase 6-2 `FieldSpec` 어휘 그대로 — registry 가 호출 직전
    검증해 잘못된 입력을 callable 에 전달하지 않는다.
  - **raw 모드** (`None`): 입력 형태가 호출마다 달라지는 도구를 위한 의도적
    escape hatch. registry 는 검증을 스킵하고, callable / 그 아래 도메인이
    자체 status (`UNSUPPORTED / MISSING_INPUT / INVALID_INPUT`) 로 잘못된 입력을
    걸러 그 결과를 Observation 으로 흡수한다.

`call(name, arguments, *, on_failure=...)` 의 흐름:

    1. 등록 안 된 이름이면 `Observation(is_failure=True, summary='unknown tool')`.
    2. schema 모드면 schema 검증 — 실패 시 callable 호출 없이 실패 Observation.
    3. raw 모드면 검증 스킵.
    4. callable(arguments) 호출.
    5. 결과를 summarize 해 Observation 으로 반환.

Phase 7-1 은 도구 자체 (callable / summarize) 는 등록만 하고 ReAct loop 가
부르도록 둔다. 도구 본체는 `tools_builtin.py` 가 채운다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional

from chat.services.agent.state import Observation
from chat.workflows.core import combine_validations, require_fields
from chat.workflows.domains.field_spec import FieldSpec


@dataclass(frozen=True)
class Tool:
    """ReAct loop 가 호출하는 도구의 메타 + callable.

    `summarize` 는 callable 의 원본 결과를 `Observation.summary` 로 줄이는 함수.
    도구 종류에 따라 list 길이만, dict 핵심 키만, status 만 등 형태가 달라
    도구마다 정의한다.
    """

    name: str
    description: str
    input_schema: Optional[Mapping[str, FieldSpec]]
    callable: Callable[[Mapping[str, Any]], Any]
    summarize: Callable[[Any], str]


# 모듈 단위 싱글톤. import 부작용으로 각 도구 모듈이 자신을 등록한다.
_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    """도구를 등록. 같은 이름을 두 번 등록하면 `ValueError`."""
    if not tool.name:
        raise ValueError('register: Tool.name 이 비어 있습니다.')
    if tool.name in _REGISTRY:
        raise ValueError(f'register: tool name={tool.name!r} 가 이미 등록되어 있습니다.')
    _REGISTRY[tool.name] = tool


def get(name: str) -> Optional[Tool]:
    return _REGISTRY.get(name)


def has(name: str) -> bool:
    return name in _REGISTRY


def all_entries() -> Iterable[Tool]:
    """등록 순서 보존 — 프롬프트 카탈로그·테스트 stable order."""
    return tuple(_REGISTRY.values())


def call(name: str, arguments: Mapping[str, Any]) -> Observation:
    """도구를 호출해 결과를 `Observation` 으로 돌려준다.

    실패 경로(미등록 / 입력 검증 실패 / callable 예외) 는 모두 `is_failure=True`
    Observation 으로 surface. ReAct loop 가 같은 처리 흐름으로 다음 iteration
    을 진행할 수 있게.
    """
    tool = _REGISTRY.get(name)
    if tool is None:
        return Observation(
            tool=name,
            summary=f'unknown tool: {name!r}',
            is_failure=True,
        )

    args = dict(arguments or {})

    if tool.input_schema is not None:
        validation = _validate_against_schema(args, tool.input_schema)
        if not validation.ok:
            problem = _format_validation(validation)
            return Observation(
                tool=name,
                summary=f'input invalid: {problem}',
                is_failure=True,
            )

    try:
        raw_result = tool.callable(args)
    except Exception as exc:                                          # noqa: BLE001
        return Observation(
            tool=name,
            summary=f'tool error: {type(exc).__name__}: {exc}',
            is_failure=True,
        )

    try:
        summary = tool.summarize(raw_result)
    except Exception as exc:                                          # noqa: BLE001
        # summarize 실패는 호출 자체는 성공한 상태라 결과는 잃되 success 로 본다 —
        # 다만 LLM 에게 "결과를 정리하지 못했다" 정도만 알린다.
        return Observation(
            tool=name,
            summary=f'tool ok, but summarize failed: {type(exc).__name__}',
            is_failure=False,
        )

    return Observation(tool=name, summary=summary, is_failure=False)


# ---------------------------------------------------------------------------
# 내부
# ---------------------------------------------------------------------------

def _validate_against_schema(
    arguments: Mapping[str, Any],
    schema: Mapping[str, FieldSpec],
) -> Any:
    """필수 필드 누락 검증. 타입별 정밀 검증은 도구 callable 의 책임."""
    required_fields = [name for name, spec in schema.items() if spec.required]
    missing = require_fields(arguments, required_fields)

    # 추가로 enum 타입은 허용 키 안에 있어야 한다.
    enum_errors: list[str] = []
    for name, spec in schema.items():
        if spec.type != 'enum' or name not in arguments:
            continue
        value = arguments[name]
        if value not in spec.enum_values:
            enum_errors.append(
                f"{name}={value!r} (허용: {list(spec.enum_values)})"
            )

    from chat.workflows.core.result import ValidationResult
    enum_part = (
        ValidationResult.fail(errors=enum_errors)
        if enum_errors else ValidationResult.success()
    )
    return combine_validations(missing, enum_part)


def _format_validation(validation: Any) -> str:
    parts: list[str] = []
    if validation.missing_fields:
        parts.append(f'missing={list(validation.missing_fields)}')
    if validation.errors:
        parts.append(f'errors={list(validation.errors)}')
    return ', '.join(parts) or 'unknown'


# ---------------------------------------------------------------------------
# 테스트 격리 helpers
# ---------------------------------------------------------------------------

def _snapshot_for_tests() -> dict[str, Tool]:
    return dict(_REGISTRY)


def _restore_for_tests(snapshot: dict[str, Tool]) -> None:
    _REGISTRY.clear()
    _REGISTRY.update(snapshot)


def _reset_for_tests() -> None:
    _REGISTRY.clear()

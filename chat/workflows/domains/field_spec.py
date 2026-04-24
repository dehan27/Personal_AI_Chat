"""Workflow 가 필요로 하는 입력 필드 선언 (Phase 6-2).

각 generic workflow 는 `WorkflowEntry.input_schema` 에 `{name: FieldSpec}` 을
선언하고, `workflow_input_extractor` 가 이 스키마를 읽어 사용자 질문에서 값을
뽑아낸다. 프로그램 어디서도 타입을 동적으로 바꾸지 않는다 — 의도를 또렷이
드러내기 위해 frozen dataclass + 문자열 리터럴 type.

지원 타입 (Phase 6-2 범위):
  - 'date'          — ISO/한국어 표기 날짜 1 개
  - 'number'        — 정수 1 개
  - 'money'         — 원 단위 금액 (단위 접미어 포함 가능)
  - 'enum'          — enum_values 의 한 키로 정규화
  - 'number_list'   — 여러 숫자 → `list[int]`

확장 후보(미구현): 'text', 'date_list', 'money_list'.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Tuple


SUPPORTED_TYPES: Tuple[str, ...] = (
    'date',
    'number',
    'money',
    'enum',
    'number_list',
)


@dataclass(frozen=True)
class FieldSpec:
    """한 필드가 어떻게 생겼는지 기술.

    - `aliases` 는 LLM fallback 프롬프트에 힌트로 주고, regex 경로에서는 직접
      쓰이지 않는다. LLM 이 "start" 와 "시작일" 을 같은 필드로 보게 해주는 용도.
    - `enum_values` 의 키는 정규화된 최종 값, 값은 자연어 토큰 튜플.
    - `default` 는 `required=False` 일 때만 의미. extractor 가 어떤 값도 못
      찾으면 `default` 를 채워준다.
    """

    type: str
    required: bool = True
    aliases: Tuple[str, ...] = ()
    default: object | None = None
    enum_values: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:  # noqa: D401
        if self.type not in SUPPORTED_TYPES:
            raise ValueError(
                f'FieldSpec.type 은 {SUPPORTED_TYPES} 중 하나여야 합니다 '
                f'(받은 값: {self.type!r}).'
            )
        if self.type == 'enum' and not self.enum_values:
            raise ValueError("FieldSpec(type='enum') 은 enum_values 가 필요합니다.")
        if self.type != 'enum' and self.enum_values:
            raise ValueError(
                "FieldSpec.enum_values 는 type='enum' 일 때만 허용됩니다."
            )

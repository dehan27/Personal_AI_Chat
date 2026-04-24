"""workflow 용 날짜 파싱·기간 계산 헬퍼 (Phase 5).

의존: `result.ValidationResult` 만. 다른 core 모듈은 import 하지 않는다.

지원 포맷 (Phase 5 §4):
- 구분자 3종: `-`, `.`, `/` — `"2025-01-31"`, `"2025.01.31"`, `"2025/01/31"`
- 2자리 연도: `"25-01-31"`, `"25.01.31"` → 2000+ 로 간주
- 한국어 자연어: `"2025년 1월 31일"`, `"2025년 01월 31일"`
- 이미 `date` / `datetime` 인스턴스 → 그대로 반환
- 실패 시 `ValueError` (도메인에서 `ValidationResult.fail` 로 번역)

out-of-scope: 상대 표현("오늘", "어제"), 타임존 문자열, 한자 연월.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Union

from chat.workflows.core.result import ValidationResult


DateLike = Union[str, date, datetime]


# "YYYY년 M월 D일" 형태. 숫자부 공백은 양쪽 허용.
_KR_PATTERN = re.compile(
    r'^\s*(\d{2,4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*$'
)

# 구분자 3종(-/./공백) 사용 YYYY-MM-DD 또는 YY-MM-DD.
_SEP_PATTERN = re.compile(
    r'^\s*(\d{2,4})[\-./](\d{1,2})[\-./](\d{1,2})\s*$'
)


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def parse_date(value: DateLike) -> date:
    """문자열 / `date` / `datetime` 을 `date` 로 정규화.

    실패 시 `ValueError`.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise TypeError(
            f'parse_date: str / date / datetime 중 하나여야 합니다 (got {type(value).__name__})'
        )

    text = value.strip()
    if not text:
        raise ValueError('parse_date: 빈 문자열은 날짜가 아닙니다.')

    match = _KR_PATTERN.match(text)
    if match:
        y, m, d = (int(g) for g in match.groups())
        return _make_date(y, m, d, source=text)

    match = _SEP_PATTERN.match(text)
    if match:
        y, m, d = (int(g) for g in match.groups())
        return _make_date(y, m, d, source=text)

    raise ValueError(f'parse_date: 지원하지 않는 형식입니다: {text!r}')


def days_between(start: DateLike, end: DateLike) -> int:
    """`end - start` 의 일 수. 순서가 뒤집혀도 음수로 그대로 돌려준다.

    양끝 포함/비포함 정책은 여기서 결정하지 않는다 — 호출측이 `+1` 할지 선택.
    """
    return (parse_date(end) - parse_date(start)).days


def months_between(start: DateLike, end: DateLike) -> int:
    """두 날짜 사이 '만' 개월 수 (연·월 차이 기반).

    예) 2024-01-15 → 2024-03-14 은 1개월 (하루 덜 채움)
        2024-01-15 → 2024-03-15 은 2개월
    """
    s = parse_date(start)
    e = parse_date(end)
    months = (e.year - s.year) * 12 + (e.month - s.month)
    # 일 기준으로 하루라도 모자라면 한 달 깎기 (양수 쪽에서만 의미 있음).
    if months > 0 and e.day < s.day:
        months -= 1
    elif months < 0 and e.day > s.day:
        months += 1
    return months


def years_between(start: DateLike, end: DateLike) -> int:
    """두 날짜 사이 '만' 년 수."""
    s = parse_date(start)
    e = parse_date(end)
    years = e.year - s.year
    if years > 0 and (e.month, e.day) < (s.month, s.day):
        years -= 1
    elif years < 0 and (e.month, e.day) > (s.month, s.day):
        years += 1
    return years


def ensure_date_order(start: DateLike, end: DateLike) -> ValidationResult:
    """시작 ≤ 종료 인지 검사. 파싱 오류도 검증 실패로 바꿔 돌려준다.

    모든 입력 문제를 한 번에 모아 반환하므로 도메인 workflow 가 위에서
    `require_fields` 결과와 `combine_validations` 하기 쉬움.
    """
    errors: list[str] = []
    try:
        s = parse_date(start)
    except (ValueError, TypeError) as exc:
        errors.append(f'시작일: {exc}')
        s = None
    try:
        e = parse_date(end)
    except (ValueError, TypeError) as exc:
        errors.append(f'종료일: {exc}')
        e = None

    if s is not None and e is not None and s > e:
        errors.append('시작일이 종료일보다 뒤입니다.')

    if errors:
        return ValidationResult.fail(errors=errors)
    return ValidationResult.success()


# ---------------------------------------------------------------------------
# 내부
# ---------------------------------------------------------------------------

def _make_date(year: int, month: int, day: int, *, source: str) -> date:
    if year < 100:
        year += 2000   # 2자리 연도는 2000 년대 기본.
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ValueError(f'parse_date: 잘못된 날짜 값입니다: {source!r}') from exc

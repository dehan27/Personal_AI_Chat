"""자연어 질문 → workflow_input (Phase 6-2).

`workflow_node` 가 dispatch 호출 직전에 이 모듈의 `extract(...)` 를 부른다.
schema 기반으로 다음 순서를 따른다:

    1) regex / 토큰 추출 (정형 케이스 고속 경로) — 날짜 / 숫자 / money / enum
    2) required 필드 중 아직 비어있으면 cheap LLM 에 fallback 요청
    3) LLM 실패·파싱 오류·타임아웃 시 regex 결과만 반환 (`MISSING_INPUT` 으로 이어짐)

의존 원칙:
    - Phase 5 core 의 `parse_date / parse_money` 는 각자 포맷 검증까지 해준다. 이
      모듈은 "질문 안에서 후보 문자열을 찾는" 역할만 맡고, 포맷 검증·정규화는
      workflow 의 `validate / execute` 단계에서 다시 보게 한다. 즉 regex 가
      잘못 찍은 후보는 workflow 가 INVALID_INPUT 으로 걸러냄 — 중복 검증 아님.
    - 순환 방지를 위해 registry / dispatch 모듈을 import 하지 않는다. schema 만
      파라미터로 받는다.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Mapping, Optional, Tuple

from chat.services.prompt_loader import load_prompt
from chat.services.single_shot.llm import run_chat_completion
from chat.services.single_shot.types import QueryPipelineError
from chat.workflows.domains.field_spec import FieldSpec


logger = logging.getLogger(__name__)


# LLM fallback 에 동원할 최근 history 수 — 너무 많으면 토큰 낭비, 맥락만 간단히.
_REWRITE_HISTORY_TURNS = 4

# 프롬프트 파일 (prompt_registry 'chat-workflow-input-extractor' 와 동일 경로).
_PROMPT_PATH = 'chat/workflow_input_extractor.md'


# ---------------------------------------------------------------------------
# 정규식 — 질문에서 후보 문자열을 찾는 데만 쓰인다. 실제 값 정규화·검증은
# 이후 workflow 의 `parse_date / parse_money / execute` 가 담당.
# ---------------------------------------------------------------------------

# YYYY-MM-DD / YYYY.MM.DD / YYYY/MM/DD / YY-MM-DD / YYYY년 MM월 DD일
_DATE_RE = re.compile(
    r'(?:\d{2,4}[-./]\d{1,2}[-./]\d{1,2})'
    r'|(?:\d{2,4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일)'
)

# '1,234,567원' / '1000원'. 캡처그룹은 숫자부만.
_MONEY_RE = re.compile(r'([\d,]+)\s*원')

# 단독 숫자(콤마 포함). 이미 money 로 매치된 구간은 호출부에서 제거한 뒤 돌린다.
_INT_RE = re.compile(r'(?<![\d.])[-+]?\d[\d,]*(?![\d.])')


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def extract(
    question: str,
    history: list,
    schema: Mapping[str, FieldSpec],
) -> Tuple[dict[str, Any], Optional[Any], Optional[str]]:
    """질문에서 workflow 에 넘길 input dict 를 만든다.

    반환: `(workflow_input, usage, model)`. LLM 이 실제로 호출된 경우에만
    `usage` / `model` 이 채워지고, 호출부(`workflow_node`)가 그걸 보고
    TokenUsage 로그를 남긴다.
    """
    if not schema:
        return {}, None, None

    text = question or ''
    extracted: dict[str, Any] = {}

    # 1) money — money 구간을 먼저 확보해야 일반 숫자와 안 겹친다.
    money_fields = _pick_fields_by_type(schema, 'money')
    money_values, money_spans = _find_money(text, limit=len(money_fields))
    for spec_name, value in zip(money_fields, money_values):
        extracted[spec_name] = value

    # 2) number / number_list — money 구간은 미리 빼고 검색.
    masked_text = _mask_spans(text, money_spans)
    number_fields = _pick_fields_by_type(schema, 'number')
    number_values, _ = _find_numbers(masked_text, limit=len(number_fields))
    for spec_name, value in zip(number_fields, number_values):
        extracted[spec_name] = value

    for name, spec in schema.items():
        if spec.type == 'number_list' and name not in extracted:
            values, _ = _find_numbers(masked_text, limit=None)
            if values:
                extracted[name] = values

    # 3) date — schema 선언 순서대로 앞에서부터 배정.
    date_fields = _pick_fields_by_type(schema, 'date')
    date_values, date_spans = _find_dates(text, limit=len(date_fields))
    for spec_name, value in zip(date_fields, date_values):
        extracted[spec_name] = value

    # 4) enum — 자연어 토큰이 매치되는 첫 키로 정규화.
    # 이 때 날짜·money 구간은 마스킹해서 "2024년" 안의 "년" 같은 토큰이 오답으로
    # 들어오는 걸 막는다.
    enum_masked_text = _mask_spans(text, list(money_spans) + list(date_spans))
    for name, spec in schema.items():
        if spec.type != 'enum' or name in extracted:
            continue
        matched = _match_enum(enum_masked_text, spec.enum_values)
        if matched is not None:
            extracted[name] = matched

    # 5) default 채우기 — required=False 이고 아직 비어있으면 default 적용.
    for name, spec in schema.items():
        if name in extracted:
            continue
        if not spec.required and spec.default is not None:
            extracted[name] = spec.default

    # 6) LLM fallback — required 인데 아직 비어있는 필드가 있으면.
    missing_required = [
        name for name, spec in schema.items()
        if spec.required and name not in extracted
    ]
    if missing_required:
        llm_output, usage, model = _call_llm_extractor(
            question=question,
            history=history,
            schema=schema,
            already_extracted=extracted,
        )
        if llm_output:
            _merge_llm_output(extracted, llm_output, schema)
        return extracted, usage, model

    return extracted, None, None


# ---------------------------------------------------------------------------
# LLM fallback
# ---------------------------------------------------------------------------

def _call_llm_extractor(
    *,
    question: str,
    history: list,
    schema: Mapping[str, FieldSpec],
    already_extracted: Mapping[str, Any],
) -> Tuple[Optional[dict[str, Any]], Optional[Any], Optional[str]]:
    """LLM 을 호출해 누락된 필드를 JSON 으로 채워오기.

    실패(네트워크 / 파싱 / 비정상 응답) 시 `(None, None, None)` 반환 —
    호출부는 regex 결과만 들고 진행한다.
    """
    try:
        system_prompt = load_prompt(_PROMPT_PATH)
    except Exception as exc:  # noqa: BLE001 — prompt 파일 없음 등
        logger.warning('workflow_input_extractor 프롬프트 로드 실패: %s', exc)
        return None, None, None

    user_payload = _format_user_payload(
        question=question,
        history=history,
        schema=schema,
        already_extracted=already_extracted,
    )
    messages = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_payload},
    ]

    try:
        raw, usage, model = run_chat_completion(messages)
    except QueryPipelineError as exc:
        logger.warning('workflow_input_extractor LLM 호출 실패: %s', exc)
        return None, None, None
    except Exception as exc:  # noqa: BLE001 — OpenAI SDK 비정형 예외 방어
        logger.warning('workflow_input_extractor LLM 예기치 못한 오류: %s', exc)
        return None, None, None

    parsed = _parse_json_object(raw)
    if parsed is None:
        logger.warning('workflow_input_extractor JSON 파싱 실패: %r', raw[:200])
        return None, usage, model

    logger.info('workflow_input_extractor LLM 보강: %r → %r', raw[:100], parsed)
    return parsed, usage, model


def _format_user_payload(
    *,
    question: str,
    history: list,
    schema: Mapping[str, FieldSpec],
    already_extracted: Mapping[str, Any],
) -> str:
    """LLM 에 건네줄 user 메시지 텍스트."""
    schema_lines = []
    for name, spec in schema.items():
        desc = f'- {name}: type={spec.type}, required={spec.required}'
        if spec.aliases:
            desc += f', aliases={list(spec.aliases)}'
        if spec.enum_values:
            desc += f', enum_keys={list(spec.enum_values.keys())}'
        if spec.default is not None:
            desc += f', default={spec.default!r}'
        schema_lines.append(desc)

    trimmed_history = _tail_history(history, _REWRITE_HISTORY_TURNS)
    history_lines = []
    if trimmed_history:
        for msg in trimmed_history:
            role = msg.get('role', '')
            content = (msg.get('content') or '').strip()
            if role and content:
                history_lines.append(f'{role}: {content}')

    parts = ['Schema:']
    parts.extend(schema_lines)
    parts.append('')
    parts.append(f'Already extracted: {json.dumps(dict(already_extracted), ensure_ascii=False)}')
    if history_lines:
        parts.append('')
        parts.append('Recent conversation:')
        parts.extend(history_lines)
    parts.append('')
    parts.append(f'Current question: {(question or "").strip()}')
    parts.append('Return JSON only:')
    return '\n'.join(parts)


def _parse_json_object(raw: str) -> Optional[dict[str, Any]]:
    """첫 `{...}` 블록을 찾아 dict 로 반환. 실패 시 None."""
    if not raw:
        return None
    text = raw.strip()
    # 코드펜스 허용하지 않는 프롬프트지만 방어.
    if text.startswith('```'):
        text = text.strip('`')
        if text.lower().startswith('json'):
            text = text[4:]
    # 첫 { ~ 마지막 } 구간.
    start = text.find('{')
    end = text.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _merge_llm_output(
    extracted: dict[str, Any],
    llm_output: dict[str, Any],
    schema: Mapping[str, FieldSpec],
) -> None:
    """LLM 이 돌려준 값을 스키마 규칙에 맞춰 `extracted` 에 반영."""
    for name, value in llm_output.items():
        if name not in schema or name in extracted:
            continue
        spec = schema[name]
        if spec.type == 'enum':
            # 정규화된 key 만 허용.
            if isinstance(value, str) and value in spec.enum_values:
                extracted[name] = value
            continue
        if spec.type in ('number', 'money'):
            try:
                extracted[name] = int(value)
            except (TypeError, ValueError):
                continue
            continue
        if spec.type == 'number_list':
            if isinstance(value, list):
                ints: list[int] = []
                for v in value:
                    try:
                        ints.append(int(v))
                    except (TypeError, ValueError):
                        continue
                if ints:
                    extracted[name] = ints
            continue
        # date / text — 문자열로만 받는다.
        if isinstance(value, str) and value.strip():
            extracted[name] = value.strip()


def _tail_history(history: list, max_messages: int) -> list:
    if not history:
        return []
    trimmed = [
        msg for msg in history
        if msg.get('role') in ('user', 'assistant') and msg.get('content')
    ]
    return trimmed[-max_messages:]


# ---------------------------------------------------------------------------
# 추출 단계별 헬퍼
# ---------------------------------------------------------------------------

def _pick_fields_by_type(schema: Mapping[str, FieldSpec], type_: str) -> list[str]:
    """스키마 선언 순서를 보존해 해당 type 필드 이름만 추출."""
    return [name for name, spec in schema.items() if spec.type == type_]


def _find_dates(text: str, limit: int) -> Tuple[list[str], list[tuple[int, int]]]:
    """질문에서 매치된 날짜 문자열들을 등장 순서대로 반환.

    실제 파싱(`parse_date`) 은 workflow 가 다시 한다 — 여기선 후보만.
    """
    if limit <= 0:
        return [], []
    values: list[str] = []
    spans: list[tuple[int, int]] = []
    for match in _DATE_RE.finditer(text):
        values.append(match.group(0).strip())
        spans.append(match.span())
        if len(values) >= limit:
            break
    return values, spans


def _find_money(text: str, limit: int) -> Tuple[list[int], list[tuple[int, int]]]:
    if limit <= 0:
        return [], []
    values: list[int] = []
    spans: list[tuple[int, int]] = []
    for match in _MONEY_RE.finditer(text):
        digits = match.group(1).replace(',', '')
        if not digits:
            continue
        try:
            values.append(int(digits))
        except ValueError:
            continue
        spans.append(match.span())
        if len(values) >= limit:
            break
    return values, spans


def _find_numbers(
    text: str,
    limit: int | None,
) -> Tuple[list[int], list[tuple[int, int]]]:
    """일반 숫자 추출. `limit=None` 이면 전부(number_list 용)."""
    values: list[int] = []
    spans: list[tuple[int, int]] = []
    for match in _INT_RE.finditer(text):
        raw = match.group(0).replace(',', '')
        try:
            values.append(int(raw))
        except ValueError:
            continue
        spans.append(match.span())
        if limit is not None and len(values) >= limit:
            break
    return values, spans


def _match_enum(
    text: str,
    enum_values: Mapping[str, Tuple[str, ...]],
) -> Optional[str]:
    """enum_values 의 토큰 중 가장 먼저 질문에 등장하는 키 반환."""
    best_pos: Optional[int] = None
    best_key: Optional[str] = None
    for key, tokens in enum_values.items():
        for token in tokens:
            pos = text.find(token)
            if pos == -1:
                continue
            if best_pos is None or pos < best_pos:
                best_pos = pos
                best_key = key
    return best_key


def _mask_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """주어진 구간을 공백으로 덮어 후속 정규식이 중복 매치하지 않게 한다."""
    if not spans:
        return text
    chars = list(text)
    for start, end in spans:
        for i in range(start, min(end, len(chars))):
            chars[i] = ' '
    return ''.join(chars)

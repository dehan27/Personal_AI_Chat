"""Agent ReAct loop 의 LLM 메시지 빌더 (Phase 7-1).

매 iteration 직전에 호출되어 system prompt + user payload 를 만든다. 시스템
프롬프트는 외부 파일(`assets/prompts/chat/agent_react.md`) 에서 읽고, user
payload 는 현재 `AgentState` (관찰·도구 호출 이력) + 도구 카탈로그 + 현재 질문
으로 구성한다.

Tool 카탈로그는 LLM 이 action 으로 부를 수 있는 이름과 입력 형태를 알도록 매
iteration 갱신해 보낸다 (도구 등록은 import 부작용이라 사실상 정적이지만, 한
turn 동안 누군가 새 도구를 register 해도 자연스럽게 반영된다).
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from chat.services.agent.state import AgentState, Observation
from chat.services.agent.tools import Tool, all_entries
from chat.services.prompt_loader import load_prompt
from chat.workflows.domains.field_spec import FieldSpec


SYSTEM_PROMPT_PATH = 'chat/agent_react.md'

# 매 iteration 의 user 메시지에 노출하는 최근 observation 개수. 너무 많으면
# 컨텍스트 비용 ↑ + 모델이 과거 정보에 끌려간다.
MAX_RECENT_OBSERVATIONS = 6


def build_messages(state: AgentState) -> list[dict[str, str]]:
    """system + user 메시지 두 개를 만들어 chat.completions 에 그대로 넘길 수 있게 반환."""
    system_prompt = load_prompt(SYSTEM_PROMPT_PATH)
    user_payload = _format_user_payload(state)
    return [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_payload},
    ]


def _format_user_payload(state: AgentState) -> str:
    sections: list[str] = []
    sections.append(f'Question: {state.question.strip()}')
    sections.append('')

    sections.append('Tools:')
    sections.extend(_describe_tool(t) for t in all_entries())
    sections.append('')

    if state.observations:
        sections.append('Recent observations:')
        for obs in state.observations[-MAX_RECENT_OBSERVATIONS:]:
            sections.append(_format_observation(obs))
        sections.append('')

    if state.tool_calls:
        last_call = state.tool_calls[-1]
        sections.append(
            f'Last tool call: {last_call.name}('
            f'{json.dumps(dict(last_call.arguments), ensure_ascii=False)})'
        )
        sections.append('')

    sections.append(
        f'iteration={state.iteration_count}, '
        f'consecutive_failures={state.consecutive_failures()}.'
    )
    sections.append('Return JSON only:')
    return '\n'.join(sections)


def _describe_tool(tool: Tool) -> str:
    if tool.input_schema is None:
        schema_part = 'arguments: free-form dict (도구 자체가 검증)'
    else:
        schema_part = (
            'arguments: ' + ', '.join(
                _describe_field(name, spec) for name, spec in tool.input_schema.items()
            )
        )
    return f'- {tool.name} — {tool.description.strip()} | {schema_part}'


def _describe_field(name: str, spec: FieldSpec) -> str:
    parts = [f'{name}({spec.type}']
    if spec.required:
        parts.append(', required')
    if spec.aliases:
        parts.append(f', aliases={list(spec.aliases)}')
    if spec.enum_values:
        parts.append(f', enum={list(spec.enum_values)}')
    parts.append(')')
    return ''.join(parts)


def _format_observation(obs: Observation) -> str:
    flag = '[FAIL] ' if obs.is_failure else ''
    return f'  - {flag}{obs.tool}: {obs.summary}'

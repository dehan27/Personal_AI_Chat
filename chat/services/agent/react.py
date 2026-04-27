"""ReAct loop runtime (Phase 7-1; Phase 7-2 smoke 후 max_iterations 6 으로 상향).

`run_agent(question, history, *, max_iterations=6) -> WorkflowResult` 가
유일한 외부 진입점. 한 iteration 흐름:

    1. `prompts.build_messages(state)` → system + user 메시지.
    2. `run_chat_completion(messages)` → JSON 한 줄 응답.
    3. `_parse_action(reply)` → `{action, arguments|answer}` 추출.
    4. action == 'final_answer'  → 종료 (FINAL_ANSWER).
       action ∈ tools             → `tools.call(...)` → Observation 누적.
       그 외                      → "unknown action" 실패 Observation.
    5. iteration_count += 1; max / 연속 실패 / 반복 호출 가드 검사.

종료 → `to_workflow_result(termination, value=..., reason=...)` 로 변환해 반환.

Phase 7-1 은 graph 와 연결되지 않으므로, 이 함수는 직접 호출(테스트·REPL)에서만
실행된다. Phase 7-2 가 `chat/graph/nodes/agent.py` 에서 같은 시그니처로 wrap.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Optional

from chat.services.agent import tools as agent_tools
from chat.services.agent.prompts import build_messages
from chat.services.agent.result import AgentTermination, to_workflow_result
from chat.services.agent.state import AgentState
from chat.services.single_shot.llm import run_chat_completion
from chat.services.single_shot.postprocess import record_token_usage
from chat.services.single_shot.types import QueryPipelineError
from chat.workflows.core import WorkflowResult


logger = logging.getLogger(__name__)


# 안전판
# Phase 7-1 은 4 로 시작했으나 7-2 smoke 검증에서 비교형 질문(retrieve A + retrieve
# B + final_answer) 패턴에 부족하다는 게 드러남 — 자료를 다 모으고도 final_answer
# 까지 못 가서 MAX_ITERATIONS_EXCEEDED 가 떨어졌음. 6 으로 상향해 retrieve 두 번 +
# 보조 검색 한 번 + final 까지 여유롭게 도달 가능하게 한다. 한 턴 LLM 호출은 최악
# rewriter 1 + agent step 7 = 8 회.
DEFAULT_MAX_ITERATIONS = 6
MAX_CONSECUTIVE_FAILURES = 3
MAX_REPEATED_CALL = 3


def run_agent(
    question: str,
    history: list,
    *,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> WorkflowResult:
    """ReAct loop 를 한 턴 돌려 `WorkflowResult` 반환."""
    state = AgentState(question=(question or '').strip(), history=list(history or []))

    if not state.question:
        return to_workflow_result(
            AgentTermination.INSUFFICIENT_EVIDENCE,
            reason='질문이 비어 있습니다.',
        )

    # JSON 파싱 retry 한 번 허용 — 같은 iteration 안에서.
    parse_retry_budget = 1

    while state.iteration_count < max_iterations:
        try:
            messages = build_messages(state)
        except Exception as exc:                                      # noqa: BLE001
            logger.warning('agent 프롬프트 구성 실패: %s', exc)
            return to_workflow_result(AgentTermination.FATAL_ERROR)

        try:
            raw, usage, model = run_chat_completion(messages)
        except QueryPipelineError as exc:
            logger.warning('agent LLM 호출 실패: %s', exc)
            return to_workflow_result(AgentTermination.FATAL_ERROR)
        except Exception as exc:                                      # noqa: BLE001
            logger.warning('agent LLM 예기치 못한 오류: %s', exc)
            return to_workflow_result(AgentTermination.FATAL_ERROR)

        if usage is not None and model:
            try:
                record_token_usage(model, usage)
            except Exception as exc:                                  # noqa: BLE001
                # TokenUsage 기록 실패는 답변 자체를 막지 않는다.
                logger.warning('agent TokenUsage 기록 실패: %s', exc)

        action = _parse_action(raw)
        if action is None:
            if parse_retry_budget > 0:
                parse_retry_budget -= 1
                state.add_observation(
                    tool='_llm',
                    summary=f'invalid JSON, retrying once: {raw[:120]!r}',
                    is_failure=True,
                )
                state.iteration_count += 1
                continue
            logger.warning('agent JSON 파싱 실패 — 종료')
            return to_workflow_result(AgentTermination.FATAL_ERROR)

        if action.get('action') == 'final_answer':
            answer = (action.get('answer') or '').strip()
            logger.info(
                'agent step %d: final_answer (answer_len=%d)',
                state.iteration_count,
                len(answer),
            )
            if not answer:
                # final_answer 인데 answer 가 비어있으면 insufficient_evidence 로 종료.
                return to_workflow_result(
                    AgentTermination.INSUFFICIENT_EVIDENCE,
                )
            state.final_answer = answer
            state.termination = AgentTermination.FINAL_ANSWER
            return to_workflow_result(
                AgentTermination.FINAL_ANSWER,
                value=answer,
            )

        tool_name = action.get('action') or ''
        arguments = action.get('arguments') or {}
        if not isinstance(arguments, Mapping):
            logger.info(
                'agent step %d: tool=%r 인자 형식 오류 (got %s)',
                state.iteration_count,
                tool_name,
                type(arguments).__name__,
            )
            state.add_observation(
                tool=tool_name,
                summary=f'arguments must be an object: got {type(arguments).__name__}',
                is_failure=True,
            )
            state.iteration_count += 1
        else:
            state.record_tool_call(tool_name, arguments)
            obs = agent_tools.call(tool_name, arguments)
            logger.info(
                'agent step %d: tool=%r args=%r → is_failure=%s summary=%r',
                state.iteration_count,
                tool_name,
                dict(arguments),
                obs.is_failure,
                obs.summary[:120],
            )
            state.observations.append(obs)
            state.iteration_count += 1

        # 종료 조건 체크 (next iteration 들어가기 전에).
        termination = _decide_termination(state, max_iterations)
        if termination is not None:
            return to_workflow_result(termination)

    # while 의 max_iterations 가드를 벗어난 경로 — 보통 위 while 조건이 먼저 잡지만 보험.
    return to_workflow_result(AgentTermination.MAX_ITERATIONS_EXCEEDED)


# ---------------------------------------------------------------------------
# 내부
# ---------------------------------------------------------------------------

def _parse_action(raw: str) -> Optional[dict[str, Any]]:
    """LLM 응답 문자열에서 첫 JSON object 를 dict 로 추출. 실패하면 None."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith('```'):
        text = text.strip('`')
        if text.lower().startswith('json'):
            text = text[4:]
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


def _decide_termination(
    state: AgentState,
    max_iterations: int,
) -> Optional[AgentTermination]:
    """다음 iteration 으로 갈지, 종료할지 판정."""
    if state.iteration_count >= max_iterations:
        return AgentTermination.MAX_ITERATIONS_EXCEEDED

    if state.consecutive_failures() >= MAX_CONSECUTIVE_FAILURES:
        return AgentTermination.NO_MORE_USEFUL_TOOLS

    if state.tool_calls:
        last = state.tool_calls[-1]
        if state.repeated_call_count(last.name, last.arguments) >= MAX_REPEATED_CALL:
            return AgentTermination.NO_MORE_USEFUL_TOOLS

    return None

# 2026-04-25 개발 로그 — 2.0.0 Phase 8-1: AgentResult / Reply Surface

## 배경

Phase 7-4 머지로 agent runtime 의 의사결정·종료 정책은 안정됐지만, **사용자에게
보이는 표면** 은 여전히 7-1 시점 그대로다.

회귀로 발견된 두 결함:

### Defect A — 출처(sources) 미노출

agent 가 retrieve 로 찾은 문서가 답변에 인용돼도 UI 의 "출처" 패널이 비어 있음.
`chat/graph/nodes/agent.py:67-72` 의 `QueryResult(... sources=[], ...)` 하드코딩
탓. single_shot 경로는 `chunk_hits` 로부터 sources 를 잘 채우지만 agent 경로는
출처가 사용자에게 도달하지 못함.

### Defect B — 결과 타입의 의미 불명

`run_agent` 가 `WorkflowResult` 를 반환해 종료 사유 (`AgentTermination`) 가
`details['termination']` 안에 dict 키로 묻혀 있음. tool_calls / sources 는 아예
타입에 자리가 없음 → agent_node 가 매번 dict 를 풀어보지 않으면 안 됨.

---

## 1. 패키지 구조 변화

```
chat/workflows/core/
  result.py                ← BaseResult Protocol (runtime_checkable) 추가

chat/services/agent/
  state.py                 ← Observation.arguments / Observation.evidence 필드 추가
  result.py                ← SourceRef / ToolCallTrace / AgentResult dataclass +
                             to_agent_result(termination, *, value, reason, state)
  tools.py                 ← args 정규화 함수 진입 직후로 이동 (5 Observation 경로
                             전부 arguments 보존) + dict result 의 'evidence' 키
                             자동 부착
  tools_builtin.py         ← _retrieve_callable 결과 dict 에 'evidence': [SourceRef(hits[0])]
                             top-1 정책으로 부착
  react.py                 ← run_agent 반환 타입 WorkflowResult → AgentResult.
                             모든 11 종료 경로가 to_agent_result(state=state) 통과
  reply.py                 ← build_reply_from_agent_result 시그니처: WorkflowResult
                             → BaseResult Protocol (AgentResult 도 받음)

chat/graph/nodes/
  agent.py                 ← QueryResult.sources = result.sources_as_dicts()
                             status 무관 노출 (NOT_FOUND 종료에도 sources 보임)
```

---

## 2. 핵심 결정

### Decision 1 — `BaseResult` Protocol 분리 + `AgentResult` 1급 시민

`WorkflowResult` 를 그대로 두고 별도 `AgentResult` 도입. 두 타입의 공통 표면
(`status / value / details`) 만 `BaseResult` Protocol 로 추출.

```python
@runtime_checkable
class BaseResult(Protocol):
    status: WorkflowStatus
    value: Any
    details: Mapping[str, Any]
```

이유:
- `AgentResult` 만의 1급 필드 (`termination / tool_calls / sources`) 가 dict 안에
  묻히지 않음.
- `reply.build_reply_from_agent_result` 같은 공용 함수는 BaseResult 만 알면 됨 →
  AgentResult / WorkflowResult 모두 받음 (구조적 매치).
- WorkflowResult 호출부 무영향 (회귀 0).

### Decision 2 — sources 정책: top-1 + status 무관 + low_relevance 제외

세 가지를 각각 다른 레이어가 책임:

| 레이어 | 책임 |
|---|---|
| `_retrieve_callable` | `hits[0]` 한 건만 evidence 후보 — top-N 다 노출하면 sources 폭주 (top-1 정책) |
| `tools.call` | callable 결과 dict 의 `'evidence'` 키 → `Observation.evidence` 튜플 부착 |
| `to_agent_result(state)` | observations 의 evidence 를 dedup `(name, url)`. `failure_kind == 'low_relevance'` obs 의 evidence 는 제외 |
| `agent_node` | `result.sources_as_dicts()` 호출만 — status 분기 없이 항상 노출 |

NOT_FOUND 종료 (max_iter / no_more_useful_tools) 여도 sources 는 그대로 — 사용자가
"이런 자료는 나왔지만 충분하지 못했다" 는 단서를 받을 수 있게 (status 무관 정책).

### Decision 3 — Observation 1:1 → ToolCallTrace

`tool_calls` 는 `state.tool_calls` (실제 호출만) 가 아니라 `state.observations` 에서
1:1 파생. `tool='_llm'` (parse 실패 step), `failure_kind='repeated_call'` (차단된
시도) 도 모두 trace 에 박힘 → 운영 디버깅 시 "왜 6 step 다 썼나" 가시화.

이를 위해 `Observation` 에 `arguments / evidence` 필드를 추가하고, `tools.call` 의
**5 종료 경로** (unknown_tool / schema_invalid / callable_error / summarize 실패 /
success) + react.py 의 **3 직접 add_observation 경로** (`_llm` parse / invalid_args
/ repeated_call) 가 모두 `arguments` 를 보존하도록 통일.

### Decision 4 — `to_agent_result` state 인자 duck-typing

`result.py → state.py` import 가 추가되면 순환 (`state.SourceRef` 가 result 에
있으므로 state 는 result 를 import). 해결:
- `state.py` 가 `result.SourceRef` 를 import (단방향).
- `result.py` 의 `to_agent_result(state)` 는 런타임 duck-typed — `.observations`
  만 접근. 타입 어노테이션은 `TYPE_CHECKING` 블록에서만 import 하고 시그니처는
  forward-string `Optional['AgentState']`.

---

## 3. 사용자-가시 변화

| 시나리오 | Before | After |
|---|---|---|
| 답변 + 출처 정상 | `sources=[]` (UI 패널 비어 있음) | `sources=[{'name':'복리후생.pdf', 'url':'/media/...'}, ...]` |
| 자료 없음 (NOT_FOUND) | sources 비어 있음 | 그동안 검색된 (의미 매치) sources 그대로 노출 — 후속 질문 단서 |
| 모든 retrieve 가 low_relevance | sources 비어 있음 | sources 비어 있음 (회귀 0 — low_relevance evidence 는 정책상 제외) |
| 답변 본문 (`reply`) | 변화 없음 | 변화 없음 (AgentResult.value 가 그대로 통과) |

---

## 4. 검증

### 단위 테스트

| 모듈 | 신규 케이스 | 누적 |
|---|---|---|
| `test_workflows_result.py` | BaseResult Protocol 매치 / 호환성 회귀 가드 | +5 |
| `test_agent_state.py` | Observation arguments / evidence 필드 회귀 | +4 |
| `test_agent_result.py` | SourceRef / AgentResult / sources_as_dicts / to_agent_result | +10 |
| `test_agent_tools.py` | 5 Observation 경로 args 보존 + evidence 부착 | +7 |
| `test_agent_tools_builtin.py` | `_retrieve_callable` top-1 + 0건 / 누락 attr | +3 |
| `test_agent_react.py` | run_agent → AgentResult / 1:1 trace / sources 정책 / 직접 경로 args | +12 |
| `test_agent_reply.py` | AgentResult 입력 케이스 (BaseResult Protocol) | +3 |
| `test_agent_node.py` | sources_as_dicts surface (OK / NOT_FOUND / 빈) | +3 |
| **총합** | | **+47** |

총 406/406 그린 (Phase 7-4 종료 시점 359 → +47).

### 회귀 가드 — 메모

- `WorkflowResult` 의 `dataclass` 필드 / 팩토리 시그니처 / `WorkflowStatus` enum
  값이 모두 동일함을 회귀 테스트로 잠금 (Phase 5 호출부 무영향).
- `tools.call` 의 5 경로가 모두 `arguments=args` 를 보존하는지 (P3 명시 회귀).
- `_retrieve_callable` 이 evidence 0건/누락 attribute 케이스에서 깨지지 않는지.

---

## 5. 후속 (Phase 8-2 / 8-3)

- **Phase 8-2 (TokenUsage Purpose / Observability)** — agent step LLM / rewriter /
  reranker 호출분의 TokenUsage 레코드에 `purpose` 메타 필드 추가. 대시보드에서
  단계별 비용 분석.
- **Phase 8-3 (BO Agent 운영 제어)** — `MAX_LOW_RELEVANCE_RETRIEVES` /
  `MAX_REPEATED_CALL` 등 runtime 가드 상수를 BO 페이지에서 즉시 변경 가능하게
  추출. 프로덕션 incident 대응 시간 단축.

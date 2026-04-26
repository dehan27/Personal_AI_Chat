# 2026-04-25 개발 로그 — 2.0.0 Phase 7-1: Agent Runtime (foundation)

## 배경

Phase 6 까지 정형 절차형 질문(`single_shot` / `workflow`)의 두 줄기가 완성됐지만 "두 자료 비교", "여러 단계 탐색", "조건 분기 많은 비교 추천" 같이 **한 번의 retrieval 이나 한 workflow 로 끝나지 않는 질문** 은 여전히 single_shot 으로 흐르거나 workflow 의 잘못된 매칭으로 어색한 답에 수렴했다.

Phase 7 의 목표는 이런 multi-hop / 탐색형 질문을 처리할 수 있는 **generic agent (ReAct) 경로** 를 여는 것 — 단, 새 회사 업무 기능이 아니라 **이미 있는 workflow / retrieval 을 도구처럼 재사용** 하는 상위 오케스트레이터.

분량이 커서 두 단계로 쪼갠다:

- **Phase 7-1 (이번)** — agent runtime 본체. AgentState · Tool 레이어 · ReAct loop · 결과 변환. **graph 와 연결하지 않음** — `ROUTE_AGENT` 는 여전히 single_shot 폴백.
- **Phase 7-2** — `chat/graph/nodes/agent.py` 와이어링 + reply 포맷터 + BO smoke + README/dev log.

**회귀 0 약속**: 7-1 머지 시점에 채팅 UI 동작은 변하지 않는다. agent 코드는 import 가능하지만 어떤 graph 경로도 그것을 호출하지 않는다.

---

## 1. 패키지 구조

```
chat/services/agent/
  __init__.py        # 패키지 안내 + tools_builtin import 부작용으로 도구 자동 등록
  state.py           # AgentState / Observation / ToolCall + bounded summaries
  tools.py           # Tool dataclass + registry + call(...)
  tools_builtin.py   # retrieve_documents / find_canonical_qa / run_workflow 등록
  prompts.py         # ReAct 메시지 빌더
  react.py           # run_agent(...) — bounded ReAct loop
  result.py          # AgentTermination + to_workflow_result 어댑터

assets/prompts/chat/
  agent_react.md     # 외부화된 ReAct system prompt (BO 편집 가능)

chat/services/
  prompt_registry.py # 'chat-agent-react' 엔트리 추가

chat/graph/nodes/
  router.py          # docstring 한 줄 — agent 는 7-2 까지 single_shot 폴백
```

---

## 2. 핵심 결정 세 가지

### 2-1. 결과 타입을 새로 만들지 않는다

설계 §12 는 status 어휘만 공유하고 별도 타입을 권장하지만, 7-1 에서는 **Phase 5 `WorkflowResult` 를 그대로 반환형으로 재사용**한다. `chat/services/agent/result.py` 에는 새 공개 dataclass 가 없고, 대신:

- `AgentTermination` enum: `final_answer / max_iterations_exceeded / no_more_useful_tools / insufficient_evidence / fatal_error`.
- `to_workflow_result(termination, *, value, reason)` 헬퍼: termination → user-facing `WorkflowResult` 변환.

매핑:

| Termination | Status | 기본 reason |
|---|---|---|
| `FINAL_ANSWER` | `OK` | (없음 — value 가 답) |
| `MAX_ITERATIONS_EXCEEDED` / `FATAL_ERROR` | `UPSTREAM_ERROR` | "잠시 후 다시 시도해 주세요." 류 |
| `NO_MORE_USEFUL_TOOLS` / `INSUFFICIENT_EVIDENCE` | `NOT_FOUND` | "더 알아볼 도구가 남지 않아..." / "근거가 부족해..." |

`UNSUPPORTED` 는 agent runtime 이 직접 만들지 않는다 — "이 workflow 는 본래 이 요청을 다루지 않음" 은 라우팅 단의 책임 (설계 §5-3, §6-2). 회귀 가드도 한 줄 (`assertNotIn('unsupported', AgentTermination 값)`).

이 결정의 trade-off는 dev log 의 "의도된 어색함" — 이름이 `WorkflowResult` 인데 agent 가 반환한다. reply 분기를 status 만 보고 처리하면 되어 변경 비용이 가장 적기에 채택. 후속 `BaseResult` 추출 리팩터는 별건.

### 2-2. Tool.input_schema 가 두 모드

```python
@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: Optional[Mapping[str, FieldSpec]]   # None = raw mode
    callable: Callable[[Mapping[str, Any]], Any]
    summarize: Callable[[Any], str]
```

- **schema 모드** (`Mapping`): registry 가 호출 직전 `require_fields` + enum 키 체크. 실패 시 callable 호출 없이 `Observation(is_failure=True, summary='input invalid: ...')`.
- **raw 모드** (`None`): 의도적 escape hatch. `run_workflow` 처럼 입력 형태가 호출마다 달라지는 도구만 사용. callable / 그 아래 도메인이 자체 status (`UNSUPPORTED / MISSING_INPUT / INVALID_INPUT`) 로 잘못된 입력을 걸러 그 결과가 Observation 으로 흡수된다.

이 분리는 design 의 "tool validation 이 한 길로 일관" 요구를 어기는 대신 "왜 raw 가 필요한지" 를 타입으로 명시함으로써 honest 하게 푼다.

### 2-3. AgentState.question 은 raw 입력

`question` 필드는 **사용자 원본 입력 그대로**. history-aware rewrite 의 적용 위치는 Phase 7-2 의 결정 — 두 옵션:

- (a) graph node 가 dispatch 직전 `rewrite_query_with_history` 를 돌려 결과를 같은 필드에 주입 (table_lookup 의 `workflow_node` 와 동일 패턴).
- (b) `AgentState` 에 `search_query` 같은 별도 필드 추가.

7-1 은 (a) 기준이라 별도 필드는 미리 도입하지 않는다 (YAGNI). 7-2 에서 (a) 로 충분치 않다는 게 드러나면 그때 (b) 로 확장.

---

## 3. ReAct loop 흐름

```
run_agent(question, history, *, max_iterations=4)
    ↓
AgentState 초기화 (question 비면 즉시 NOT_FOUND)
    ↓
while iteration_count < max_iterations:
    build_messages(state)               ← system + user payload
    run_chat_completion(messages)        ← gpt-4o-mini
    record_token_usage(...)              ← 매 LLM 호출
    _parse_action(reply) → JSON dict (실패 시 1회 retry, 두 번째 실패 → FATAL_ERROR)
    if action == 'final_answer':
        FINAL_ANSWER → to_workflow_result(value=answer) → OK
    if action ∈ tools:
        tools.call(name, args) → Observation 누적 → iteration_count += 1
    _decide_termination(state):
        iteration_count >= max_iterations → MAX_ITERATIONS_EXCEEDED
        consecutive_failures >= 3        → NO_MORE_USEFUL_TOOLS
        repeated_call_count >= 3         → NO_MORE_USEFUL_TOOLS
```

**안전판**:
- `MAX_ITERATIONS = 4` (코드 상수, BO 토글은 후속 Phase).
- `MAX_CONSECUTIVE_FAILURES = 3`.
- `MAX_REPEATED_CALL = 3` — `(name, sorted(args))` 키로 동일 호출 카운팅 → 같은 도구 같은 인자로 무한 반복 차단.
- LLM JSON 파싱은 **1회 retry**. 두 번째 실패 시 `FATAL_ERROR`.

### Observation 길이 제한

`MAX_OBSERVATION_SUMMARY_CHARS = 600`. 한 도구 결과 요약이 이를 넘으면 끝에 `…` 붙여 truncate. 이렇게 하지 않으면 다음 iteration 의 LLM 컨텍스트가 도구 응답으로 잠식된다.

---

## 4. 첫 도구 세 개

| 이름 | 모드 | 호출 | 요약 |
|---|---|---|---|
| `retrieve_documents` | schema (`{'query': text, required}`) | `chat.services.single_shot.retrieval.retrieve_documents(query)` | "<N>건, 첫 출처: <파일명> — '...'" |
| `find_canonical_qa` | schema (`{'query': text, required}`) | `chat.services.single_shot.qa_cache.find_canonical_qa(query)` | "<N>건, top similarity=<x> — 질문: '...'" |
| `run_workflow` | **raw** (None) | `chat.workflows.domains.dispatch.run(arguments['workflow_key'], arguments.get('input', {}))` | `WorkflowResult.status` + value 짧게 |

세 도구 모두 import 시점에 `chat/services/agent/tools_builtin.py` 가 자동 register. 이 모듈은 `chat/services/agent/__init__.py` 가 import 부작용으로 들여놓는다 — `chat.workflows.domains.general` 패턴과 동일.

---

## 5. ReAct system prompt

`assets/prompts/chat/agent_react.md` — Phase 1 registry 에 `chat-agent-react` 키로 등록돼 BO Prompt 페이지에서 즉시 편집 가능. 핵심 규약:

- 매 step **JSON 한 줄** 출력. markdown / 코드펜스 / 산문 금지.
- `action` 은 catalog 의 정확한 이름 또는 `final_answer`.
- 같은 도구 같은 인자를 연속으로 두 번 부르지 않는다.
- 자료 부족이면 정직하게 `final_answer` 로 종료 + "자료를 찾지 못했습니다" 류 응답.

`prompts.build_messages(state)` 가 user payload 에 동봉:
- 현재 `Question:`
- `Tools:` — 등록된 도구 목록(설명 + 인자 schema 또는 free-form 표시)
- `Recent observations:` — 최근 6 개. 실패는 `[FAIL]` 접두.
- `Last tool call:` — 직전 호출 (반복 회피 힌트)
- `iteration=N, consecutive_failures=K`
- `Return JSON only:` 마무리

---

## 6. 테스트

신규 6 파일 / **63 케이스** (전체 `chat.tests` 264 → 286). 모두 LLM 호출 없이 mock.

| 파일 | 케이스 | 주요 커버 |
|---|---|---|
| `test_agent_result.py` | 9 | termination → status 매핑 + 회귀 가드(`UNSUPPORTED` 미생산) |
| `test_agent_state.py` | 10 | Observation truncate / consecutive_failures / repeated_call_count(인자 순서 무관) |
| `test_agent_tools.py` | 10 | schema 검증 / raw 모드 우회 / unknown 이름 / callable 예외 / summarize 예외 / enum 거부 |
| `test_agent_tools_builtin.py` | 9 | 세 도구 등록 + 실 모듈 위임(mock) + 0건 / unsupported / missing_input 모두 Observation 으로 표면화 |
| `test_agent_prompts.py` | 9 | 카탈로그 / raw-mode 표기 / observation cap / iteration 카운터 / 마지막 호출 echo |
| `test_agent_react.py` | 13 | immediate final / one-tool / max_iterations / repeated call / 연속 실패 / unknown action / JSON retry / 두 번 실패 / LLM 예외 / 빈 질문 short-circuit / 빈 answer / non-dict args / TokenUsage 호출 횟수 |

---

## 7. 검증

### 자동
```bash
docker compose exec -T web python manage.py check
docker compose exec -T web python manage.py test chat.tests
```

286/286 green. 기존 케이스 단 한 건도 깨지지 않음.

### 수동 (REPL — graph 미연결이라 채팅 UI 로는 불가)

`OPENAI_API_KEY` 가 살아있고 관련 문서가 업로드돼 있다는 전제로:

```python
>>> from chat.services.agent.react import run_agent
>>> r = run_agent('이 두 문서 중 어디가 휴가가 더 길어?', history=[])
>>> r.status
<WorkflowStatus.OK | NOT_FOUND | UPSTREAM_ERROR>
>>> r.value      # OK 일 때만 의미 있음
```

7-2 가 graph 에 연결되기 전까지 채팅창에서 "agent route" 라는 keyword 로 매치된 질문도 실제 응답은 single_shot 그대로다 — `chat/graph/nodes/router.py` docstring 에 명시.

### 회귀 민감 포인트

- `ROUTE_AGENT` 는 여전히 single_shot 폴백 → 채팅 UI 동작 변화 없음.
- `chat/services/agent/__init__.py` import 가 다른 모듈에 부작용을 일으키지 않음 (자기 패키지 내 등록만).
- 새 status 도 추가되지 않음 — Phase 6-3 의 여섯 값(`OK / MISSING_INPUT / INVALID_INPUT / UNSUPPORTED / NOT_FOUND / UPSTREAM_ERROR`) 그대로.
- TokenUsage 추가 호출은 agent 가 실제로 실행될 때만 — 본 PR 단계에선 0.

---

## 8. 리스크 메모 (실제 7-2 에서 다듬을 것)

- LLM 이 카탈로그 외 도구 이름을 호출 → `Observation(unknown tool)` 만 누적 → 연속 실패 한도로 종료.
- 같은 도구 같은 인자 반복 → `repeated_call_count` 가드.
- Observation 폭주 → 600자 cap + 최근 6개만 LLM 컨텍스트에.
- LLM JSON 출력 깨짐 → 1회 retry → 두 번째 실패 시 `UPSTREAM_ERROR`.
- TokenUsage 기록 실패 → 답변 자체는 막지 않음 (table_lookup 패턴 동일).
- 결과 타입 재사용의 어색함 → README + 본 dev log 명시. 후속 `BaseResult` 추출 리팩터 후보.

---

## 9. 완료 정의 충족

- [x] `chat/services/agent/` 패키지 — 6 모듈 모두 import 가능.
- [x] `WorkflowResult` 재사용 + `AgentTermination` + `to_workflow_result(...)` 만 새로 도입. agent 패키지에 새 공개 dataclass 없음.
- [x] `Tool.input_schema` schema 모드 / raw 모드 명시화. `run_workflow` 만 raw.
- [x] `AgentState.question` 은 raw 입력 — rewrite 위치는 7-2 결정.
- [x] ReAct loop 안전판 3종 (max_iterations / 연속 실패 / 반복 호출) 동작.
- [x] LLM 호출마다 `record_token_usage`.
- [x] chat UI / graph / BO / migration 변경 없음 (회귀 0).
- [x] 286/286 테스트 green.

---

## 10. Phase 7-2 로 미루는 것

- `chat/graph/nodes/agent.py` 신규 + `chat/graph/app.py` ROUTE_AGENT 와이어링.
- agent 응답 reply 포맷터 (근거·도구 사용 요약 포함) — `chat/workflows/domains/reply.py` 확장 또는 `chat/services/agent/reply.py` 분리.
- `query_rewriter` 통합 (table_lookup 패턴 동일).
- BO RouterRule 의 agent route 활성화 + 채팅 smoke.
- 사용자-facing dev log 추가 (Phase 7 완료 노트 포함).

이후 Phase 8+ 후보:

- `BaseResult` 추출 리팩터 (`WorkflowResult` / `AgentResult` 가 상속).
- 사람 승인 (HITL).
- 멀티 에이전트 협업 / 자가 계획 분해.
- 외부 SaaS observability (Datadog / Langfuse 등).
- 회사 전용 agent tool / 도메인.
- TokenUsage 에 호출 목적 메타 필드 (`single_shot / rewriter / extractor / table_lookup / agent_step`).
- 장시간 백그라운드 실행 / 비동기 streaming.

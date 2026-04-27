# 2026-04-27 개발 로그 — 2.0.0 Phase 7-2: Agent Graph Wiring (Phase 7 완료)

## 배경

Phase 7-1 이 generic agent runtime 을 isolated 형태로 들였다 (`chat/services/agent/`,
286/286 단위 테스트). 다만 `chat/graph/app.py` 의 `ROUTE_AGENT` 매핑은 single_shot
폴백을 유지했고, agent 결과를 사용자 reply 로 변환하는 포맷터도 없었다.

Phase 7-2 의 목표는 **runtime 을 graph 와 view 까지 끝까지 잇기**. 새 도구·새 기능
없이 결선·reply·smoke 만. 회귀 0 약속: 7-2 머지 시점에 single_shot / workflow 경로
동작은 변하지 않는다.

---

## 1. 패키지 구조 변화

```
chat/services/agent/
  reply.py            ← 신규: build_reply_from_agent_result(WorkflowResult) -> str

chat/graph/nodes/
  agent.py            ← 신규: agent_node(state) — rewrite + run_agent + reply 합성

chat/graph/
  app.py              ← 수정: add_node('agent', agent_node) + ROUTE_AGENT → 'agent' + add_edge END

chat/graph/nodes/
  router.py           ← 수정: docstring "agent_node 가 처리한다" 로 갱신

chat/services/agent/
  __init__.py         ← 수정: docstring "graph 와 연결됨" + Phase 7-1/7-2 차이 명시

chat/tests/
  test_agent_reply.py            ← 신규 (9 케이스)
  test_agent_node.py             ← 신규 (8 케이스)
  test_graph_agent_wiring.py     ← 신규 (4 케이스, graph 단 자동 회귀 첫 사례)
```

---

## 2. 핵심 결정 세 가지 (Plan §1~5 그대로)

### 2-1. agent_node 는 workflow_node 의 거울

`chat/graph/nodes/workflow.py` 가 이미 정착된 패턴을 따른다 — history 가 있으면
`rewrite_query_with_history` 로 self-contained 검색어 생성 후 그 결과를
`run_agent(effective_question, history=history)` 의 첫 인자로 넘긴다. raw
`state.question` 은 view·logger·history 가 그대로 보게 두고, rewriter 결과는
지역 변수로만 흐른다 (Phase 7-1 의 AgentState.question 옵션 (a) 와 일관).

TokenUsage 이중 기록 방지: `run_agent` 내부에서 매 LLM 호출 후 이미
`record_token_usage` 가 동작 중이라, agent_node 는 rewriter 호출분만 추가 기록.

### 2-2. reply 포맷터를 별도 모듈로 분리

`chat/services/agent/reply.py` 신규. `chat/workflows/domains/reply.py` 와 합치지
않은 이유:

- workflow reply 는 `_ok_formatters` 가 `workflow_key` 별로 분기 — agent 는 key 가
  없다. 한 모듈에 끼면 hack 이 늘어난다.
- agent 가 만들 수 있는 status 는 OK / NOT_FOUND / UPSTREAM_ERROR 셋. workflow 의
  여섯 status 와 분기 형태가 다르다.

`MISSING_INPUT / INVALID_INPUT / UNSUPPORTED` 도달 시 `ValueError` raise — 의도된
**fail-fast invariant guard**. agent runtime 이 status 어휘를 잘못 확장한
regression 을 친절히 숨기지 않는다 (Plan §5-1 의 정책 그대로).

### 2-3. graph 결선 자동 회귀 테스트 (`test_graph_agent_wiring.py`)

7-1 머지 시점까지 `chat.tests` 어디에서도 `run_chat_graph` 를 호출하지 않았다.
즉 conditional edge 매핑 / `add_node` / `add_edge` 의 오타·누락을 자동으로
잡아내는 테스트가 0 건이었다. 본 PR 이 그 첫 사례.

patch target 이 정확해야 작동한다 (잘못 잡으면 patch 가 무시되고 실제 DB/LLM
경로를 탄다):

- 라우팅 강제: `chat.graph.nodes.router.route_question` — `router_node` 가
  모듈 상단에서 `from chat.services.question_router import route_question` 으로
  가져오므로 binding 은 nodes.router 쪽.
- 노드 stub: `chat.graph.app.<node_name>` — `app.py` 가 노드 함수들을 import
  해서 그 binding 으로 `add_node` 에 박는다. 노드 모듈 쪽
  (`chat.graph.nodes.X.X_node`) 을 patch 하면 graph 가 들고 있는 binding 은 안
  바뀐다.
- 컴파일 캐시: `_compiled_graph` 가 `lru_cache(maxsize=1)` 라 patch 후 반드시
  `cache_clear()` 호출 → patch 된 binding 으로 재컴파일. 테스트 종료 시
  `addCleanup(_compiled_graph.cache_clear)` 로 다음 테스트 격리.

ROUTE_AGENT / ROUTE_SINGLE_SHOT / ROUTE_WORKFLOW 세 분기를 stub 노드로 한 케이스씩
+ ROUTE_AGENT 외 라우팅에서 agent_node 호출 0 검증.

---

## 3. Smoke 검증에서 발견된 7-1 튜닝 부족분

Plan 의 §검증 단계에서 비교형 질문 (`경조사 규정과 휴가 규정 비교` 류) 을 실제
채팅 UI 로 던진 결과 **세 회 연속 회귀**를 발견. 모두 7-1 의 boundary / 가시성이
보수적이었던 데서 기인:

### 3-1. `MAX_ITERATIONS=4` 가 비교 패턴에 부족

증상: agent 가 `retrieve_documents` 만 4회 부르고 `MAX_ITERATIONS_EXCEEDED` →
`UPSTREAM_ERROR` ("도구를 너무 많이 사용했어요").

분석: 비교 질문은 retrieve A + retrieve B + final 패턴에 최소 3 step 필요. 4 는
한 번 더 검색하고 싶은 LLM 의 욕구를 완전히 막을 수 없는 수치.

수정: `DEFAULT_MAX_ITERATIONS = 4 → 6`. 한 턴 최대 LLM 호출 ≈ rewriter 1 + agent
step 7 = 8회.

### 3-2. retrieve_documents observation 이 LLM 에 데이터를 못 노출

증상: agent 가 retrieve 4회 모두 non-failure (3건/4건/2건/4건 검색됨) 했는데도
`final_answer` 가 "자료를 찾지 못했습니다" 로 종결.

분석: `_summarize_retrieve` 가 첫 청크 본문 80자만 노출. 회사 규정 문서의 첫 80자
는 보통 헤더 (`복리후생 규정 제O조에 따라 ...`) 이고 실제 표 값
(`본인 결혼 100만원, 자녀 결혼 50만원`) 은 청크 안쪽. LLM 은 데이터를 손에 쥐고도
값을 못 봐서 정직하게 "못 찾았다" 응답.

대조: single_shot 은 retrieve 결과 청크 전체를 LLM context 에 박아 한 번에
답변하므로 같은 한계가 없다. 이건 agent 의 ReAct 구조 (summarize-then-synthesize)
에 내재된 lossy 포맷의 부산물.

수정 두 가지 (같은 commit):
- `MAX_OBSERVATION_SUMMARY_CHARS: 600 → 1500` — 한 턴 컨텍스트 = 6 step × 1500자
  ≈ 9000자, gpt-4o-mini 128k 한도에 여유.
- `_summarize_retrieve`: 첫 청크 80자 → top 3 청크 × 400자. 출처 + 본문 일부
  모두 노출.

### 3-3. 프롬프트가 결정성 부족

증상: 도구 비실패 호출 후에도 LLM 이 "한 번 더 검색해보자" 모드로 들어감.

수정: `agent_react.md` 에 두 가지 항목 추가:
- **Decisiveness rules**: "비실패 도구 호출 2회 후 다음은 반드시 final_answer.
  비교 질문은 retrieve A 1회 + retrieve B 1회 + final."
- **Reading retrieval observations**: "스니펫에 값 있으면 final 에 그대로 사용
  ('자료를 찾지 못했습니다' 로 거짓 종결 금지). 스니펫이 헤더만 보이고 값이 없으면
  query 다듬어 retrieve 한 번 더 — 청크 안쪽 truncation 영역에 값이 있을 가능성."

### 3-4. iteration 가시성 부족

증상: smoke 디버깅 첫 시도 시 서버 로그에 agent 가 어떤 도구를 어떤 인자로
불렀는지 한 줄도 없어서 진단 불가.

수정: `react.py` 에 step 별 INFO 로그 — `agent step N: tool=... args=... →
is_failure=... summary=...`. workflow_node 의 `logger.info('workflow 실행: ...')`
와 동일 패턴. 운영 가시성 확보.

### 3-5. Phase 7-3 으로 분리

본 PR 의 _summarize_retrieve 는 "top N 청크의 첫 K 자" 라는 임의 cap 에 머문다.
즉 데이터가 K+1 자 위치에 있으면 같은 문제 재발. 근본 해결은 **query 키워드
매치 위치 주변 ±윈도우** 잘라 보내기 (Google 검색 결과 페이지의 snippet 기법).
이건 별건의 IR 작업이라 **Phase 7-3** 으로 분리:

- 제목 후보: `Phase 7-3 — Agent Retrieval Snippet Windowing`
- 범위: `_summarize_retrieve` + `_focus_window(content, query, length)` helper.
  키워드 매치 → 매치 주변 ±L/2 잘라냄 / 미매치 → 청크 첫 L자 fallback.
- 외 변경 없음.

---

## 4. ReAct loop 흐름 (현재 상태)

```
ROUTE_AGENT
    ↓
agent_node(state):
    history 있음?
        ├─ yes → rewrite_query_with_history(question, history) → effective_question
        │       (rewriter usage 잡히면 record_token_usage)
        └─ no  → effective_question = raw question
    ↓
run_agent(effective_question, history=history) [Phase 7-1, max_iterations=6]
    ↓
    while iter < 6:
        build_messages(state) — system + recent observations (cap 6) + tools catalog
        run_chat_completion(messages) → JSON
        record_token_usage(...)
        _parse_action(reply)
            ├─ final_answer → AgentTermination.FINAL_ANSWER → OK
            ├─ tool ∈ {retrieve_documents, find_canonical_qa, run_workflow}
            │   → tools.call → Observation (summary ≤ 1500자, retrieve 면 top 3 × 400자)
            └─ unknown → 실패 Observation
        _decide_termination:
            iter ≥ 6                 → MAX_ITERATIONS_EXCEEDED → UPSTREAM_ERROR
            consecutive_failures ≥ 3 → NO_MORE_USEFUL_TOOLS    → NOT_FOUND
            repeated_call_count ≥ 3  → NO_MORE_USEFUL_TOOLS    → NOT_FOUND
    ↓
build_reply_from_agent_result(WorkflowResult) [Phase 7-2]:
    OK            → str(value)
    NOT_FOUND     → details['reason'] or 기본 카피
    UPSTREAM_ERROR → details['reason'] or 기본 카피
    그 외 status   → ValueError (fail-fast invariant)
    ↓
QueryResult(reply=..., sources=[], total_tokens=0, chat_log_id=None)
```

---

## 5. 테스트

신규 3 파일 / **24 케이스** (전체 `chat.tests` 286 → **310**). 모두 LLM·DB
호출 없이 mock.

| 파일 | 케이스 | 주요 커버 |
|---|---|---|
| `test_agent_reply.py` | 9 | OK value pass-through / NOT_FOUND·UPSTREAM_ERROR reason pass / 기본 카피 / MISSING/INVALID/UNSUPPORTED → ValueError |
| `test_agent_node.py` | 8 | history 빈 경우 rewriter 호출 0 / 있는 경우 rewritten 결과로 run_agent / record_token_usage 호출 횟수 / OK·NOT_FOUND·UPSTREAM_ERROR 모두 QueryResult 로 직렬화 / TokenUsage 기록 실패가 답변 막지 않음 / 반환 dict 형태 |
| `test_graph_agent_wiring.py` | 4 | ROUTE_AGENT → agent_node / ROUTE_SINGLE_SHOT → single_shot_node / ROUTE_WORKFLOW → workflow_node / non-agent 라우팅에서 agent_node 호출 0 |

추가 보강된 기존 테스트 (Section 3 의 튜닝 회귀 가드):
- `test_agent_tools_builtin.py` 에 retrieve summary 가 실제 값을 노출하는지 / 청크
  cap / per-chunk truncate 등 3 케이스 신규.
- `test_agent_react.py` 의 `test_max_iterations_exceeded_returns_upstream_error`
  를 새 max_iterations=6 에 맞춰 갱신.

---

## 6. 검증

### 자동
```bash
docker compose exec -T web python manage.py test chat.tests
```

**310/310 green**. 7-1 머지 직후 286 + 신규 24. 기존 단위 테스트 한 건도 깨지지
않았다.

`python manage.py check` 통과.

### 사용자-facing smoke (BO + 채팅 UI)

`/bo/router-rules/` 에 `route='agent'` contains 룰 두 개 등록:
- `비교` (priority 50)
- `더 유리` (priority 50)

채팅 UI 의 시나리오 통과:

| 시나리오 | 결과 |
|---|---|
| **agent 매치 + 자료 충분** (`결혼 경조금이랑 자녀 경조금 비교`) | 통과 — ReAct 가 retrieve 1~2회 후 final_answer 로 한국어 비교 답변. 서버 로그에 `agent step 0 / 1 / 2: ...` 라인과 `agent 실행: status=ok` 확인. |
| **agent 매치 + LLM 정직 응답** | 통과 — 자료 부족 비교 질문에서 LLM 이 "자료를 찾지 못했습니다" 류 final_answer 직접 작성. status=OK 그대로 노출 (NOT_FOUND 카피 아님 — 7-1 mapping 의 의도된 동작). |
| **non-agent 매치 회귀** (`경조사 규정 알려줘`, `2025-01-01부터 며칠?`) | 통과 — 7-1 머지 직후와 답변 동일. 서버 로그에 `agent 실행:` 라인 없음 (agent_node 호출 0). |
| **agent 매치 + fatal** | 미수행 — OPENAI_API_KEY 무효화 시나리오는 운영 환경 영향이 있어 단위 테스트 (`test_agent_react.py:test_llm_exception_becomes_upstream_error`) 로 가드. |

NOT_FOUND smoke 는 운영 재현이 어려워 단위 테스트
(`test_repeated_same_call_terminates_with_not_found` /
`test_consecutive_tool_failures_trigger_no_more_useful_tools`) 로만 보장.

### 회귀 민감 포인트

- single_shot / workflow 분기: 답변·토큰 사용량 변화 0.
- ROUTE_AGENT 매치 없는 질문: agent_node 호출 0 (자동 테스트로 가드).
- TokenUsage 추가 호출은 agent route 진입 시에만 (rewriter + agent step LLM).
- `chat.services.agent` import 부작용 변화 없음 — `tools_builtin` 자동 등록은 7-1 그대로.

---

## 7. 의도된 어색함 (그대로 유지)

7-1 에서 채택한 "agent 가 `WorkflowResult` 를 반환한다" 의 어색함은 7-2 에서도
유지. 이름이 `WorkflowResult` 인데 agent 가 만든다는 건 의미적으로 어색하지만,
reply 분기를 status 만 보고 처리하는 단순함이 변경 비용을 압도. 후속
`BaseResult` 추출 리팩터는 별건 (Phase 8+ 후보).

---

## 8. Phase 7 완료 + Phase 7-3 예고

Phase 7 의 4 가지 핵심 목표 (설계 §1) 가 다 충족됐다:

- [x] single_shot / workflow 로 안 풀리는 탐색형 질문을 처리할 새 경로 — agent
- [x] ReAct 패턴 기반 반복 실행을 LangGraph 안에 도입 — agent_node
- [x] agent 가 직접 모든 걸 해결하지 않고 기존 workflow / service 를 도구처럼
      재사용 — `retrieve_documents` / `find_canonical_qa` / `run_workflow`
- [x] 회사 전용 업무 로직이 아니라 범용 multi-hop 능력 — 새 도메인 0

**Phase 7 마일스톤 닫기**.

### Phase 7-3 분리 작업 (예정)

본 PR 의 §3-5 에서 분리한 query-focused snippet windowing. 작은 PR 1개 예상:

- `_summarize_retrieve` 에서 query 키워드 매치 위치 찾기.
- 매치 위치 주변 ±L/2 잘라 보내기. 미매치면 청크 첫 L자 fallback.
- Tool API 변경 없음 (`_retrieve_callable` 의 반환을 dict 로 감싸 `Any` 우회).

### 그 외 후속 Phase 후보 (8+)

- `BaseResult` 추출 리팩터.
- 사람 승인 (HITL).
- 멀티 에이전트 / 자가 계획 분해.
- 외부 SaaS observability (Datadog / Langfuse).
- 회사 전용 agent tool / 도메인.
- TokenUsage 에 호출 목적 메타 필드.
- 장시간 백그라운드 실행 / 비동기 streaming.
- agent reply 에 retrieval 출처 / 사용 도구 요약 surface.
- BO 에서 `max_iterations` / 도구 카탈로그 / 활성 토글.

---

## 9. 완료 정의 충족

- [x] `chat/graph/nodes/agent.py` import 가능, `agent_node(state) -> {'result': QueryResult}`.
- [x] `chat/graph/app.py` ROUTE_AGENT 매핑이 새 노드로 향함 — single_shot 폴백 제거.
- [x] `chat/services/agent/reply.py` 가 OK / NOT_FOUND / UPSTREAM_ERROR 한국어 reply.
- [x] MISSING/INVALID/UNSUPPORTED 도달 시 ValueError — 단위 테스트 가드.
- [x] `agent_node` 가 history 있을 때만 `rewrite_query_with_history` 호출.
- [x] graph 결선 자동 회귀 테스트 (`test_graph_agent_wiring.py`) 가 ROUTE_* 세 분기 검증.
- [x] 단위 테스트 24 건 신규, 전체 310 green.
- [x] 채팅 UI smoke 의 §6 시나리오 (자료 충분 / LLM 정직 응답 / non-agent 회귀) 통과.
- [x] README §3-1 + §11 갱신 + 본 dev log.
- [x] Phase 7 마일스톤의 모든 이슈 (#43, #45) 닫힘 → 마일스톤 닫기.

# 2026-04-27 개발 로그 — 2.0.0 Phase 7-4: Agent Decision Policy & Retrieval Relevance (Phase 7 완료)

## 배경

Phase 7-3 머지 직후 smoke 검증에서 windowing 본 무대 밖의 결함 두 가지가 노출됐다.

### Defect 1 — 관련 없는 query 에서 무한 retrieve 회로

질문: `우주여행 비교` (회사 문서에 우주여행 자료 없음).

```
step 0~5: retrieve_documents 매번 다른 변형 query 로 호출, 모두 비실패 5건 반환
→ MAX_ITERATIONS_EXCEEDED → UPSTREAM_ERROR ("도구를 너무 많이 사용했어요")
```

원인: retriever 가 cosine similarity 상 가장 가까운 청크 N건을 항상 비실패로 반환 (실제 관련성 없어도). LLM 은 "결과 있다, query 만 다듬자" 로 해석.

### Defect 2 — 같은 (tool, args) 핑퐁

질문: `복리후생 규정 비교` (broad query).

```
step 0: '복리후생 규정 비교'
step 1: '복리후생 규정 세부사항'
step 2: '복리후생 규정 비교'      ← step 0 동일
step 3: '복리후생 규정 세부사항'  ← step 1 동일
step 4: '복리후생 규정 비교'      → MAX_REPEATED_CALL=3 trigger → NOT_FOUND
```

원인: LLM 이 두 query 핑퐁. 프롬프트의 "동일 인자 연속 호출 금지" 가 무시됨. `prompts.build_messages` 가 직전 한 건만 노출해 LLM 이 step 0 / step 2 동일성을 못 인지.

Phase 7-4 가 두 결함을 LLM 협조 없이도 알고리즘적으로 차단.

---

## 1. 패키지 구조 변화

```
chat/services/agent/
  state.py            ← Observation.failure_kind 필드 + low_relevance_retrieve_count() 메서드
  tools.py            ← Tool.failure_check optional + tools.call 종류별 failure_kind 세팅
  tools_builtin.py    ← _LOW_SIGNAL_TOKENS / _earliest_match / _has_meaningful_match
                        + _summarize_retrieve 마커 부착 + _retrieve_failure_check 등록
  prompts.py          ← build_messages: last 1 tool call → last 5 + No call repetition 가이드
  react.py            ← MAX_LOW_RELEVANCE_RETRIEVES=3 + 동일 args 차단 + _decide_termination 우선순위 재배치

assets/prompts/chat/
  agent_react.md      ← Decisiveness rules: No call repetition + Reading retrieval observations: 마커 인지
```

---

## 2. 핵심 결정 — runtime guard 세 갈래

### Part 1 — 동일 (tool, args) 호출 차단 (Defect 2 정조준)

`react.py` 가 LLM 의 동일 args 호출을 검사해 **실제 callable 실행을 차단** + `failure_kind='repeated_call'` Observation 만 누적. deterministic 도구라 retry 무의미.

```python
elif state.repeated_call_count(tool_name, arguments) >= 1:
    state.add_observation(
        tool=tool_name,
        summary='이전과 같은 인자로 이미 호출했습니다...',
        is_failure=True,
        failure_kind='repeated_call',
    )
```

### Part 2 — `Tool.failure_check` + retrieve 의 low-relevance 마킹 (Defect 1 정조준)

`Tool` dataclass 에 optional `failure_check: Optional[Callable[[Any], bool]]` 추가 (additive — 기존 호출 호환). `tools.call` 가 callable 정상 반환 후 호출, True 면 `Observation(is_failure=True, failure_kind='low_relevance')`.

**예외 처리 계약**: `failure_check` 자체 버그가 자기충족적 종료 spiral 만들지 않게 try/except 로 감싸 — 예외 시 `logger.warning('failure_check error: ...')` + not-failure 로 폴백.

retrieve_documents 에 `_retrieve_failure_check` 등록:

```python
def _retrieve_failure_check(result):
    """0-hit OR 모든 hit 가 _has_meaningful_match=False 면 failure 처리."""
    query = (result or {}).get('query') or ''
    hits = (result or {}).get('hits') or []
    if not hits:
        return True  # 0건도 "no useful evidence" 로 동일 취급.
    return not any(_has_meaningful_match(h.content, query) for h in hits)
```

### Part 3 — `MAX_LOW_RELEVANCE_RETRIEVES=3` 누적 가드 + 우선순위 재배치 (UPSTREAM_ERROR 미도달 보장)

Part 2 만으로는 LLM 이 query 변형 + tier-OR false positive 가 끼어들면 consecutive_failures 가 리셋돼 max_iter 도달 가능. 누적 카운터 + 우선순위 재배치로 강화.

`AgentState.low_relevance_retrieve_count` 신규:
```python
def low_relevance_retrieve_count(self) -> int:
    return sum(
        1 for obs in self.observations
        if obs.tool == 'retrieve_documents'
        and obs.failure_kind == 'low_relevance'
    )
```

`_decide_termination` 우선순위 재배치 — **low_rel 가드를 max_iter 보다 먼저** 평가:
```python
def _decide_termination(state, max_iterations):
    # 1) low_relevance 누적 — 마지막 step 동시 도달 시 NOT_FOUND 우선.
    if state.low_relevance_retrieve_count() >= MAX_LOW_RELEVANCE_RETRIEVES:
        return AgentTermination.NO_MORE_USEFUL_TOOLS
    # 2) max_iter 안전판.
    if state.iteration_count >= max_iterations:
        return AgentTermination.MAX_ITERATIONS_EXCEEDED
    # 3) 연속 실패 / 4) 동일 호출 반복 (Part 1 차단으로 사실상 도달 불가).
    ...
```

---

## 3. 알고리즘적 보장 검증

### 보장 범위

**low_relevance failure 가 3회 이상 발생하는 시나리오에서 max_iter UPSTREAM_ERROR 미도달**.

worst case: LLM 이 매번 다른 args 로 retrieve, tier-OR false positive 가 매 두 step 마다 끼어드는 경우:

| step | 동작 | low_rel | consecutive | iter |
|---|---|---|---|---|
| 0 | retrieve fail | 1 | 1 | 1 |
| 1 | retrieve false-pos | 1 | 0 | 2 |
| 2 | retrieve fail | 2 | 1 | 3 |
| 3 | retrieve false-pos | 2 | 0 | 4 |
| 4 | retrieve fail | **3 → fires** | 1 | 5 |

→ NO_MORE_USEFUL_TOOLS → NOT_FOUND. **max_iter=6 도달 안 함**.

### 의도된 예외

LLM 이 6 step 모두 false positive (성공) 만 받는 케이스 → max_iter UPSTREAM_ERROR. 이건 retrieve 가 의미 없는 결과를 반환하지 않는 상태에서 LLM 이 final_answer 결정을 못 하는 진짜 LLM 결함이라 운영적으로도 UPSTREAM_ERROR 가 맞음.

---

## 4. failure_kind 분리 (자료 없음 vs 실행 오류)

`tools.call` 이 종류별로 `failure_kind` 세팅:
- `failure_check True` → `'low_relevance'`
- callable 예외 → `'callable_error'`
- schema validation fail → `'schema_invalid'`
- unknown tool → `'unknown_tool'`
- (summarize 예외는 7-1 정책 그대로 — `is_failure=False` + 폴백 summary, `failure_kind=None`. 본 Phase 범위 밖.)

`react.py` 가 직접 추가하는 obs:
- `arguments` non-Mapping → `'invalid_args'`
- 동일 (tool, args) call-block → `'repeated_call'`

`low_relevance_retrieve_count` 는 **`failure_kind == 'low_relevance'` 만 카운트** — 실행 오류 (callable_error / schema_invalid / repeated_call) 가 우연히 3회 누적돼도 NOT_FOUND 로 종료되지 않음. "자료 없음" 과 "실행 오류" 분리.

---

## 5. relevance 마커 — strict longest-tier 정책

### `_LOW_SIGNAL_TOKENS` 블랙리스트

의문/비교/요청류 일반 토큰 (예: `비교`, `차이`, `얼마`, `알려줘`) 를 frozenset 로 명시. windowing 매치는 이 토큰도 후보로 쓰지만 (자리 잡기에 유용), relevance 판정에서는 제외.

### `_has_meaningful_match` — longest meaningful token tier 매치

```python
def _has_meaningful_match(content, query):
    tokens = _tokenize_query(query)
    meaningful = [t for t in tokens if t.lower() not in _LOW_SIGNAL_TOKENS]
    if not meaningful:
        return False
    max_len = len(meaningful[0])
    longest_tier = [t for t in meaningful if len(t) == max_len]
    lower = content.lower()
    for token in longest_tier:
        if lower.find(token.lower()) >= 0:
            return True
    return False
```

- `우주여행 비용 비교`: meaningful=[`우주여행`(4), `비용`(2)], longest_tier=[`우주여행`]. `우주여행` 미매치 + `비용` 매치 → False (Defect 1 핵심 가드).
- `결혼 휴가`: 둘 다 2자, longest_tier=[`결혼`,`휴가`]. 한쪽만 매치 → True (입력 순서 영향 최소화).

**알려진 한계**: 동률 max_len 토큰 중 일반 도메인 단어가 우연히 매치되는 케이스 (예: `우주여행 프로그램 비교` 의 `프로그램`(4) 만 매치) 는 True 로 잡힘. 이 회귀는 Part 3 의 `MAX_LOW_RELEVANCE_RETRIEVES=3` 누적 가드가 보완 방어선.

### `_summarize_retrieve` 마커 부착

- hit 별로 `_has_meaningful_match=False` → `[관련성 낮음] ` prefix
- 모든 hit 미매치 → summary 머리에 `[query 핵심 토큰 매치 없음 — 관련 자료 부족 가능성]` 라인

LLM 은 이 신호를 보고 final_answer 로 정직 종료할 기회를 얻고, 무시해도 Part 3 의 누적 가드가 종료시킴.

---

## 6. recent tool calls 노출 + 프롬프트 가이드

### `build_messages` — last 1 → last K=5

이전: `Last tool call: retrieve_documents({"query": "..."})`
신규:
```
Recent tool calls (last 5):
  1. retrieve_documents({"query": "..."})
  2. retrieve_documents({"query": "..."})
  3. retrieve_documents({"query": "..."})
  ...
Do NOT repeat any of the above (tool, arguments) combinations.
```

K=5 결정 근거: max_iter=6 의 거의 전체 history 노출. dict args 라 줄당 50~100자, 5줄 합쳐도 토큰 비용 무시 가능. 0건일 땐 `Recent tool calls (none yet).`

### `agent_react.md` 가이드 두 군데

1. Decisiveness rules — **No call repetition**: "Recent tool calls 리스트의 어떤 (tool, args) 와도 일치하면 안 됨. runtime 도 차단하지만 거기에 의존하는 건 낭비."
2. Reading retrieval observations — **마커 인지**: "[관련성 낮음] 청크는 핵심 의미 토큰 미매치. 머리 마커 [query 핵심 토큰 매치 없음 ...] 가 있으면 corpus 에 자료 없는 것 — query 다듬어 retry 하지 말고 final_answer."

---

## 7. 테스트

신규 31건 / 전체 `chat.tests` 331 → **362** (실제 신규 31, 일부 기존 케이스도 새 정책에 맞춰 갱신).

| 클래스 | 케이스 | 주요 커버 |
|---|---|---|
| `EarliestMatchTests` | 4 | windowing 위치 계산 — 매치 / 미매치 / 빈 input / 긴 토큰 우선 |
| `HasMeaningfulMatchTests` | 6 | strict 정책 — longest 매치 True / 짧은 의미만 False / 동률 tier OR / low-signal only False / 빈 query / 모두 low-signal |
| `RetrieveSummaryRelevanceMarkerTests` | 3 | 마커 부착 — 모두 매치 / 모두 미매치 / 일부 매치 |
| `RetrieveFailureCheckTests` | 3 | failure_check True/False / **0-hit → low_relevance** (P3) |
| `ObservationFailureKindTests` | 2 | default None / 명시 보존 |
| `LowRelevanceRetrieveCountTests` | 3 | low_relevance 만 / 다른 kind 무시 / 다른 도구 무시 |
| `ToolsFailureKindTests` | 4 | None / True (low_relevance) / 예외 폴백 / callable_error 분리 |
| `ToolsUnknownToolKindTests` | 1 | unknown_tool kind |
| `RecentToolCallsTests` | 3 | none yet / 1건 / 6+ 일 때 last 5 |
| `RuntimeGuardPart1Tests` | 1 | 동일 args 차단 → consecutive_failures 한도 |
| `RuntimeGuardCumulativeTests` | 2 | 누적 3 도달 / **마지막 step 동시 도달 시 NOT_FOUND 우선** (P2-1 직접 검증) |

추가로 기존 테스트 3건 갱신 (`test_delegates_to_single_shot_retrieval` / `test_zero_results_summary` / `test_repeated_same_call_terminates_with_not_found`) — 새 정책에 맞춰 expectation 변경.

---

## 8. 검증

### 자동
```bash
docker compose exec -T web python manage.py test chat.tests
docker compose exec -T web python manage.py check
```

**362/362 green**. 7-3 의 windowing 테스트 17건 한 건도 깨지지 않음 (`_focus_window` 외부 동작 변경 0).

### 사용자-facing smoke 결과

7-3 시점 smoke 룰 그대로 (`비교` / `더 유리`).

| 시나리오 | 7-3 결과 | 7-4 결과 | 정상? |
|---|---|---|---|
| `우주여행 비교` | UPSTREAM_ERROR ("도구를 너무 많이...") | NOT_FOUND + "질문에 맞는 자료를 찾을 수 없었습니다..." | ✓ low_relevance 누적 가드 발동 |
| `복리후생 규정 비교` | NOT_FOUND (max_repeated) | NOT_FOUND + "충분한 답을 만들지 못했습니다. 더 구체적인 질문으로..." | ✓ broad query — 카피로 사용자 안내 |
| `결혼 경조금 비교` (broad) | OK 또는 NOT_FOUND (확률적) | NOT_FOUND + "충분한 답을 만들지 못했습니다..." | ✓ 모호한 query — 안내 |
| `결혼 경조금이랑 자녀 결혼 경조금 비교해줘` (specific) | OK + 비교 답변 | OK + 비교 답변 | ✓ 회귀 0 |
| `우주여행 비용 비교` | UPSTREAM_ERROR | NOT_FOUND + "찾을 수 없었습니다..." | ✓ P2-1 핵심 (longest meaningful 미매치) |

### Smoke 에서 발견한 UX 이슈와 보강

**현상**: 시나리오 2 / 짧은 시나리오 3 같은 broad query 에서 retrieve 마다 same-domain 청크 (예: `복리후생 규정.pdf` 첫 페이지) 가 첫 hit 으로 반복 noun. 의미 매치 True 라 low_relevance 가드 안 걸림 → max_iter=6 도달 → 이전 정책에선 UPSTREAM_ERROR.

**문제**: UPSTREAM_ERROR 의 "잠시 후 다시 시도" 카피가 부정확 — 같은 query 재시도해도 동일 결과 (LLM 결정 부족).

**보강 (이 PR 내 추가 커밋)**:
- `to_workflow_result`: `MAX_ITERATIONS_EXCEEDED` 매핑을 `UPSTREAM_ERROR` → **`NOT_FOUND`** 로 변경. FATAL_ERROR (LLM/네트워크 일시 오류) 만 진짜 UPSTREAM_ERROR.
- `_DEFAULT_REASONS` 카피 정밀화:
  - `MAX_ITERATIONS_EXCEEDED`: "**충분한 답을 만들지 못했습니다. 더 구체적인 질문으로 다시 물어봐 주세요.**"
  - `NO_MORE_USEFUL_TOOLS`: "**질문에 맞는 자료를 찾을 수 없었습니다. 질문을 다시 한 번 확인해 주세요.**"
  - `INSUFFICIENT_EVIDENCE`: "관련 자료를 충분히 확인하지 못했습니다. 질문을 다시 한 번 확인해 주세요."

이 변경으로 broad/모호 query 에 대한 사용자 응답이 "잠시 후 재시도" (부정확) 가 아니라 **"질문 다시 다듬어 주세요"** 라는 정확한 안내가 됨.

**알려진 한계 (Phase 8+ 후보)**: broad query 에서 LLM 이 retrieve 만 6 step 채우는 패턴 자체는 알고리즘적으로 막지 않음 — content overlap 검출 / 더 강한 프롬프트 압박은 별 PR 의 일.

---

## 9. 회귀 가드

- 7-3 의 `_focus_window` 단위 테스트 17건 한 건도 안 깨짐.
- 기존 retrieve / agent_node / reply / graph wiring 테스트 모두 그대로 통과 (수정 3건은 새 정책에 맞춰 expectation 갱신).
- `Tool.failure_check=None` default 라 기존 도구 (`find_canonical_qa`, `run_workflow`) obs.is_failure 동작 변경 0.
- 새 `MAX_LOW_RELEVANCE_RETRIEVES=3` 누적 가드는 **retrieve_documents 전용** — 정상 retry 케이스 영향 없음.
- agent 가 만들 수 있는 status 어휘 변경 0 (`OK / NOT_FOUND / UPSTREAM_ERROR` 그대로).
- TokenUsage 호출 패턴 / 횟수 변화 0.

---

## 10. Phase 7 (2.0.0 Agent) 진짜 종료

- **7-1** (#43, 머지): runtime + tools + 단위 테스트. graph 미연결.
- **7-2** (#45, 머지): graph wiring + reply 포맷터 + BO smoke + 7-1 튜닝.
- **7-3** (#47, 머지): query-focused snippet windowing.
- **7-4 (이 PR, #49)**: agent decision policy + retrieval relevance awareness.

**Phase 7 마일스톤 닫기** (이번엔 진짜 종료).

---

## 11. Phase 8+ 후보

- `BaseResult` 추출 리팩터.
- HITL.
- 멀티 에이전트 / 자가 계획 분해.
- 외부 SaaS observability (Datadog / Langfuse).
- 회사 전용 agent tool / 도메인.
- TokenUsage 에 호출 목적 메타 필드.
- 한국어 형태소 분석기 (KoNLPy / Mecab) — 토큰화 정밀도.
- BM25 / 임베딩 기반 sub-chunk re-ranking — `_has_meaningful_match` 의 tier-OR limitation 보완.
- workspace + `read_chunk(idx)` 도구 분리 — summarize-then-synthesize 의 lossy 한계 근본 재설계.
- agent reply 의 sources / 도구 사용 요약 surface.
- BO 에서 `max_iterations` / `MAX_LOW_RELEVANCE_RETRIEVES` / 도구 카탈로그 / agent 활성 토글.

---

## 12. 완료 정의 충족

- [x] `_LOW_SIGNAL_TOKENS` 블랙리스트 + `_has_meaningful_match` (longest meaningful tier) strict 정책.
- [x] `_summarize_retrieve` per-chunk `[관련성 낮음]` + 머리 `[query 핵심 토큰 매치 없음]` 마커.
- [x] `Observation.failure_kind` 분리 — 자료 없음 / 실행 오류.
- [x] `Tool.failure_check` (additive) + try/except 예외 폴백 + retrieve 0-hit / all-low-relevance failure 마킹.
- [x] `state.low_relevance_retrieve_count` — low_relevance 만 strict 카운트.
- [x] `MAX_LOW_RELEVANCE_RETRIEVES=3` 누적 가드 + `_decide_termination` 우선순위 재배치.
- [x] `react.py` 동일 (tool, args) 호출 runtime block + `failure_kind='repeated_call'`.
- [x] `build_messages` last K=5 + No call repetition 가이드.
- [x] `agent_react.md` 마커 인지 + No call repetition 가이드.
- [x] 단위 테스트 31건 신규, 전체 362 green.
- [x] README §3-1 + §11 + 본 dev log.
- [x] **알고리즘적 invariant**: low_relevance failure 3회+ 발생 시 max_iter UPSTREAM_ERROR 미도달.

# 2026-04-27 개발 로그 — 2.0.0 Phase 7-3: Agent Retrieval Snippet Windowing (Phase 7 완료)

## 배경

Phase 7-2 머지 직후, 사용자가 smoke 디버깅 중 직접 지적:

> "180자라면 만약에 181번째부터 내용이있으면 또 같은 문제인거네?"

7-2 의 `_summarize_retrieve` 는 청크 첫 N자 (snippet 길이) 만 노출하는 fixed-position truncation. 답이 N+1 자 이후에 있으면 LLM 은 절대 못 봄. 7-2 가 cap 600 → 1500, snippet 80 → 400자 로 늘려 표면적 증상은 완화했지만, 임의 cap 자체가 **같은 종류의 회귀를 항상 가짐** (트레이드오프 일 뿐 해결 아님).

Phase 7-3 의 목표: **query 키워드 매치 위치 주변 ±윈도우** 잘라 보내기. 검색 엔진 (Google 결과 페이지 등) 이 쓰는 표준 IR snippet 기법. 같은 토큰 예산 (top 3 × 400자) 으로 관련성 높은 부분을 보여줌.

회귀 0 약속의 정확한 정의는 §3 표 참고 — 답변 능력 측면 회귀 0 + byte-identical 출력은 일부 케이스에서만 보장.

---

## 1. 패키지 구조 변화

```
chat/services/agent/tools_builtin.py
  ├─ _TOKEN_STRIP_CHARS, _KEYWORD_MIN_LEN     ← 신규 상수
  ├─ _tokenize_query(query)                   ← 신규 module-private
  ├─ _focus_window(content, query, *, length) ← 신규 module-private
  ├─ _retrieve_callable(arguments)            ← 수정: 반환 dict wrapping
  └─ _summarize_retrieve(result)              ← 수정: dict 받아 _focus_window 호출

chat/tests/test_agent_tools_builtin.py
  ├─ TokenizeQueryTests (5)                   ← 신규
  ├─ FocusWindowTests (12)                    ← 신규
  └─ RetrieveDocumentsToolTests (+4)          ← windowing 회귀 보강
```

---

## 2. 핵심 결정

### 2-1. Tool API 시그니처는 그대로 — `_retrieve_callable` 반환을 dict 로 감싸기

`Tool.summarize: Callable[[Any], str]` 시그니처를 `Callable[[Any, Mapping], str]` 로 바꾸면 가장 깔끔하지만, 다른 도구 (`find_canonical_qa`, `run_workflow`) 와 향후 추가될 도구 모두에 영향. retrieve 한정 격리 변경 전략으로 처리:

```python
def _retrieve_callable(arguments):
    query = arguments['query']
    return {'query': query, 'hits': _retrieve(query)}

def _summarize_retrieve(result):                 # signature 그대로
    query = (result or {}).get('query') or ''
    hits  = (result or {}).get('hits') or []
    ...
```

**장점**: Tool 데이터타입 / `tools.call` 흐름 / 다른 도구 영향 0. 외부에 노출되는 건 `Observation.summary` 문자열뿐.

**Trade-off (수용)**: 형태가 retrieve 만 dict — 일관성 떨어짐. 단 본 우회는 tools_builtin.py 한 모듈 안에서만 보이고, 기존 retrieve 테스트는 `_retrieve` (모듈 함수) 를 mock 하므로 dict wrapping 영향 0 (테스트 한 건도 안 깨짐). 다른 도구도 query-aware summarize 가 필요해지면 그때 Tool API 시그니처 변경을 한 번에 진행.

### 2-2. `_tokenize_query` — 길이 내림차순 정렬

```python
def _tokenize_query(query):
    tokens = []
    for raw in query.split():
        cleaned = raw.strip(_TOKEN_STRIP_CHARS)
        if len(cleaned) >= _KEYWORD_MIN_LEN:
            tokens.append(cleaned)
    tokens.sort(key=len, reverse=True)           # stable — 같은 길이 입력 순서 유지
    return tokens
```

- 공백 split + 양 끝 punctuation strip (`결혼?` → `결혼`, `"경조금"` → `경조금`).
- 길이 ≥ 2 필터 — 1자 조사/어미/단일 stopword 제거.
- **길이 내림차순 정렬** — 긴 토큰일수록 도메인 키워드일 확률이 높음. 매치 시 긴 토큰 위치 우선 → 짧은 일반 토큰 ("비교", "있는", "하는") 이 청크 앞부분에 우연히 걸려 관련 없는 윈도우를 고르는 회귀 차단.

한국어 형태소 분석기 (KoNLPy / Mecab) 미도입 — 의존성 비용 vs 효용. 운영 데이터에서 부족이 입증되면 후속 Phase 검토.

### 2-3. `_focus_window` — forward-bias 단일 정책

```python
def _focus_window(content, query, *, length):
    # ... edge cases ...
    tokens = _tokenize_query(query)
    earliest = -1
    for token in tokens:                          # 정렬 덕분에 첫 매치 = 가장 긴 매치 토큰
        idx = content.lower().find(token.lower())
        if idx >= 0:
            earliest = idx
            break

    if earliest < 0:
        return content[:length] + '…'             # 미매치 — 7-2 fallback 동일

    pre = length // 4
    start = max(0, earliest - pre)                # 매치가 청크 매우 앞이면 자연 클램프 → 0
    end   = min(len(content), start + length)
    start = max(0, end - length)                  # content 끝 닿으면 윈도우 길이 보존

    snippet = content[start:end]
    prefix = '…' if start > 0 else ''
    suffix = '…' if end < len(content) else ''
    return prefix + snippet + suffix
```

**의도적으로 `if earliest < length:` 강제 분기를 두지 않음**. 초안에 그게 있었지만, "키워드는 350자, 값은 450자" 같은 흔한 표 패턴 (예: "본인 결혼" 다음에 "100만원") 에서 본 목적인 401자+ 답 노출을 깨뜨림. forward-bias 한 줄기로 처리하면 `start = max(0, earliest - pre)` 의 자연 클램프 덕분에:

| earliest | start | 출력 |
|---|---|---|
| earliest < length//4 (~100) | 0 (자연 클램프) | 첫 N자 — 7-2 byte-identical |
| length//4 ≤ earliest | earliest - pre | 매치 주변 윈도우 |

---

## 3. 회귀 가드 — 회귀 0 의 정확한 정의

`length=400` 기준:

| earliest 위치 | 7-2 출력 | 7-3 출력 | 동등성 |
|---|---|---|---|
| `0 ≤ earliest < length//4` (예: 0~99) | 청크 0~400자 | **청크 0~400자 (자연 클램프)** | byte-identical (회귀 0) |
| `length//4 ≤ earliest < length` (100~399) | 청크 0~400자 | 매치 주변 윈도우 (예: 250~649) | 출력 다름. 값이 length 너머 (~450 등) 에 있어도 노출 가능 — **본 목적 일부** |
| `earliest ≥ length` (400+) | 청크 0~400자 (답 못 봄) | 매치 주변 윈도우 (예: 500~899) | 출력 다름. **본 목적** — 이전 못 보던 답 노출 |
| 매치 미발견 | 청크 0~400자 | 청크 0~400자 + `…` | byte-identical (회귀 0) |

**핵심**: 출력 텍스트 byte-identical 회귀 0 은 (a) 매치가 청크 매우 앞 (`< length//4`) / (b) 키워드 매치 미발견 두 케이스에서만 보장. 그 외 구간에서는 출력 텍스트가 의도적으로 달라지지만 그건 답변 능력 향상이 본 목적이라 의도된 변화. 답을 노출하던 시나리오는 항상 답을 계속 노출.

추가 가드:
- `MAX_OBSERVATION_SUMMARY_CHARS=1500` 그대로 — 한 턴 컨텍스트 budget 변화 없음.
- Tool API signature 변경 0 — 다른 도구 / 외부 코드 영향 없음.
- 비-retrieve 도구 (`find_canonical_qa`, `run_workflow`) summary 변경 0.
- `_retrieve` 함수 자체 호출 수 / 인자 변경 0 — retrieval 결과는 동일.

---

## 4. 테스트

신규 21건 / 전체 `chat.tests` 310 → **331**:

| 클래스 | 케이스 | 주요 커버 |
|---|---|---|
| `TokenizeQueryTests` | 5 | 빈 query / punctuation strip / 1자 토큰 컷 / 길이 내림차순 / stable sort |
| `FocusWindowTests` | 12 | 빈 content / content < length / 빈 query / 1자 토큰 only / 매치 매우 앞 byte-identical / **키워드 350자 + 값 450자 경계 케이스** / 매치 length 너머 / 끝 근처 anchor / 미매치 fallback / case-insensitive / **긴 토큰 우선** / **punctuation strip** |
| `RetrieveDocumentsToolTests` (보강 +4) | 4 | 401자+ 위치 키워드 windowing 발동 / 미매치 fallback byte-identical / 한국어 키워드 / 긴 토큰 우선순위 |

플랜은 FocusWindow ≈13 라 했지만 토큰화 동작을 별도 `TokenizeQueryTests` 클래스로 격리 분리해서 사실상 12+5=17 으로 더 정밀하게 커버.

전체 회귀: 7-2 의 286+24=310 + 21 신규 = **331/331 green**.

---

## 5. 검증

### 자동
```bash
docker compose exec -T web python manage.py test chat.tests
docker compose exec -T web python manage.py check
```

331/331 green. `python manage.py check` 통과. 7-2 의 retrieve 회귀 테스트 (`test_summary_exposes_top_chunk_contents_for_llm` 등) 한 건도 깨지지 않음.

### 사용자-facing smoke (수동)

7-2 시점 smoke 룰 그대로 (`비교` / `더 유리`).

7-2 까지는 채팅 UI 비교형 질문 ("결혼 경조금이랑 자녀 경조금 비교") 이 잘 답하는 케이스가 있고 안 답하는 케이스가 있었다 — 답이 첫 400자 안에 있느냐 없느냐에 따라. 7-3 적용 후 매치 위치가 청크 어디든 윈도우가 그쪽으로 이동하므로 이 분포 의존성이 사라짐.

본 PR 의 windowing 변경은 LLM 컨텍스트 텍스트만 바뀌고 모델 / 도구 호출 패턴은 그대로라 응답 시간·토큰 사용량 변화는 미미.

### 회귀 민감 포인트

- single_shot / workflow 분기: 답변·토큰 사용량 변화 0.
- 비-retrieve agent 도구 (`find_canonical_qa`, `run_workflow`) summary 변경 0.
- `Tool` dataclass / `tools.call` 흐름 변경 0.
- agent_node / reply 포맷터 / graph wiring 변경 0.

---

## 6. Phase 7 (2.0.0 Agent) 진짜 종료

7-1 / 7-2 / 7-3 누적 산출물:

- **7-1**: `chat/services/agent/` 6 모듈 (state / tools / tools_builtin / prompts / react / result) + ReAct loop + 3 도구. graph 미연결.
- **7-2**: `chat/graph/nodes/agent.py` + `chat/services/agent/reply.py` + `app.py` 결선 + graph 결선 자동 회귀 테스트 첫 사례 + 7-1 튜닝 (max_iterations 6, observation cap 1500, retrieve top 3×400자, 프롬프트 결정성 강화, iteration INFO 로그).
- **7-3 (이 PR)**: query-focused snippet windowing — `_tokenize_query` + `_focus_window` + `_retrieve_callable` dict wrapping.

설계 문서 (`resources/plans/2.0.0_Phase 7_Agent_개발_설계.md`) 의 4 가지 핵심 목표 모두 충족 + 7-2 smoke 가 노출한 retrieval observation 의 fixed-position truncation 한계까지 근본 해결. **Phase 7 마일스톤 닫기**.

---

## 7. 후속 Phase 후보

본 PR 의 `_focus_window` 는 IR 측면에서 가장 단순한 단일-매치 forward-bias 윈도우. 향후 검토 가치 있는 개선:

- 한국어 형태소 분석기 (KoNLPy / Mecab) — "결혼경조금" 처럼 띄어쓰기 안 한 query 도 매치.
- BM25 / 임베딩 기반 sub-chunk re-ranking — 단순 keyword find 가 아니라 의미 유사도로 윈도우 위치 결정.
- 다중 매치 윈도우 합성 — 한 청크 안에 키워드가 멀리 떨어진 두 위치에 있을 때 두 윈도우 결합.
- workspace + `read_chunk(idx)` 도구 분리 패턴 — summarize-then-synthesize 의 lossy 한계를 근본 재설계. retrieve 결과 전체를 workspace 에 두고 LLM 이 필요할 때 fetch.
- BO 에서 `_RETRIEVE_SNIPPET_LEN` / `_RETRIEVE_TOP_N` 토글.

그 외 Phase 7 종료 시점에 대기 중인 후속 작업:

- `BaseResult` 추출 리팩터 (`WorkflowResult` / `AgentResult` 가 이를 상속).
- 사람 승인 (HITL).
- 멀티 에이전트 / 자가 계획 분해.
- 외부 SaaS observability (Datadog / Langfuse).
- 회사 전용 agent tool / 도메인.
- TokenUsage 에 호출 목적 메타 필드 (`single_shot / rewriter / extractor / table_lookup / agent_step`).
- agent reply 에 retrieval 출처 / 사용 도구 요약 surface.
- BO 에서 `max_iterations` / 도구 카탈로그 / agent 활성 토글.

---

## 8. 완료 정의 충족

- [x] `_tokenize_query` 가 punctuation strip + 길이≥2 + 길이 내림차순 정렬 수행.
- [x] `_focus_window` 가 forward-bias 윈도우 단일 정책으로 (a) 매치 < length//4 → 첫 N자 (7-2 byte-identical), (b) length//4 ≤ 매치 < length → 윈도우 이동 (값이 length 너머에도 노출), (c) 매치 ≥ length → 윈도우 이동 (본 목적), (d) 미매치 → 첫 N자 fallback 처리.
- [x] `_retrieve_callable` 반환이 `{'query', 'hits'}` dict.
- [x] `_summarize_retrieve` 가 query 받아 hit 마다 windowing 적용.
- [x] `Tool.summarize` 시그니처 / 다른 도구 / Tool dataclass 변경 0.
- [x] 단위 테스트 21 신규, 전체 331 green. 7-2 의 retrieve 테스트 한 건도 깨지지 않음.
- [x] README §3-1 + §11 갱신 + Phase 7-3 dev log.
- [x] Phase 7 마일스톤의 모든 이슈 (#43, #45, #47) 닫힘 → 마일스톤 다시 닫기 (이번엔 진짜 종료).

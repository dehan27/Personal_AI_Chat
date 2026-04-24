# 2026-04-24 개발 로그 — 2.0.0 Phase 4-3: Retrieval Contextualization

## 배경

Phase 4-2 머지 직후 실사용 중 재현된 회귀:

```
user: 경조사 규정 알려줘
bot : (경조사 전체 규정 — 본인 상 500만원, 배우자 상 100만원 ...)
user: 비싼거
bot : 자료에 따르면, 비싼 경조사 항목은 외주용역 구매 요청(Turnkey-5억 이상)입니다 ...   ❌
```

라우터는 정상(single_shot 분류). 문제는 `chat/services/single_shot/pipeline.py:35-38` 가 retrieval 에 현재 질문 하나만 넘긴다는 점이었다.

```python
chunk_hits = retrieve_documents(question)   # history 안 씀
qa_hits    = find_canonical_qa(question)    # 여기도 안 씀
messages   = build_single_shot_messages(question, chunk_hits, qa_hits, history)  # LLM 에만 history
```

즉 **retrieval 은 single-turn, LLM 은 multi-turn** 이 어긋나 있었다. "비싼거" 가 그대로 `search_chunks` 에 들어가 "비싼" 이 포함된 엉뚱한 문서(5억 외주용역 결재 조항)가 상위로 잡히고, LLM 은 제공된 자료 범위 안에서만 답을 낼 수 있으니 결국 맥락이 있는데도 잘못된 답을 내뱉은 것.

Phase 4-3 은 retrieval **앞단에 쿼리 재작성 단계를 하나 삽입**해 이 회귀를 해소한다.

---

## 1. 패키지 구조

```
chat/
  services/
    query_rewriter.py            # 신규 — 재작성 helper
    prompt_registry.py           # 'chat-query-rewriter' 엔트리 추가
    single_shot/
      pipeline.py                # rewrite → retrieval/qa_cache → LLM
  tests/                         # 기존 tests.py → 패키지로 전환
    __init__.py
    test_query_rewriter.py
assets/prompts/chat/
  query_rewriter.md              # 재작성용 시스템 프롬프트 (BO 편집 가능)
```

---

## 2. 파이프라인 변경

`run_single_shot` 맨 위에 한 블록 추가:

```python
search_query, rewriter_usage, rewriter_model = rewrite_query_with_history(
    question, history,
)
if rewriter_usage is not None and rewriter_model is not None:
    record_token_usage(rewriter_model, rewriter_usage)

chunk_hits = retrieve_documents(search_query)
qa_hits    = find_canonical_qa(search_query)
...
messages   = build_single_shot_messages(question, chunk_hits, qa_hits, history)
```

포인트:
- **원본 `question` 은 그대로** LLM 프롬프트 / ChatLog / UI 에 흐름. 사용자가 입력한 문구를 보존.
- `search_chunks` / `find_canonical_qa` 시그니처는 건드리지 않음. 재작성된 쿼리든 원본이든 하나의 문자열로 받는다.
- 재작성 호출이 실제로 일어난 경우에만 `record_token_usage` 를 불러 대시보드에서 동일하게 집계된다.

---

## 3. `query_rewriter` 동작

```python
def rewrite_query_with_history(question, history) -> (str, usage | None, model | None):
    if not history:
        return question, None, None         # 첫 질문 — LLM 호출 생략

    history_slice = history[-REWRITE_HISTORY_TURNS:]
    try:
        raw, usage, model = _call_rewriter_llm(question, history_slice)
    except (QueryPipelineError, Exception):
        return question, None, None         # 장애 시 원본 유지

    cleaned = _clean_llm_output(raw)        # 접두어 / 따옴표 제거
    if not cleaned or _is_noop(cleaned):
        return question, usage, model       # NOOP 응답 — 원본 유지 (usage 는 있으니 기록)

    if len(cleaned) > 200:
        return question, usage, model       # 탈선 방어

    return cleaned, usage, model
```

- **`REWRITE_HISTORY_TURNS = 6`** — 사용자 3턴 + 어시스턴트 3턴. 전체를 넘기면 오래된 맥락이 노이즈가 되고 토큰도 늘어난다.
- **`NOOP` sentinel** — 프롬프트 규칙상 질문이 이미 자립적이면 `NOOP` 만 출력하도록 강제. 파싱 비용 없이 조기 종료.
- **실패 fallback** — OpenAI 장애 / 포맷 이상 / 공백 응답 / 200자 초과 → 모두 원본 질문을 그대로 반환. 회귀 0.

---

## 4. 프롬프트 외부화

`assets/prompts/chat/query_rewriter.md` 에 시스템 프롬프트를 분리하고 `prompt_registry` 에 엔트리를 추가했다.

```python
PromptEntry(
    key='chat-query-rewriter',
    title='검색어 재작성 프롬프트',
    description='후속 질문이 "비싼거" 처럼 맥락에 의존할 때 ...',
    relative_path='chat/query_rewriter.md',
),
```

운영자가 BO Prompt 관리에서 바로 튜닝할 수 있어 코드 재배포 없이 품질 조정이 가능하다.

프롬프트 본문은 다음 네 규칙 + 세 가지 예시로 구성:
- 한국어 한 줄, 장식 기호 / 접두어 / 따옴표 금지
- 자립 질문이면 정확히 `NOOP`
- 대화에 없는 사실 추가 금지
- 최소 키워드 유지 — 문장이 아니라 검색어

---

## 5. 로그·관측

- 정상 재작성: `INFO chat.services.query_rewriter: 쿼리 재작성: '비싼거' → '경조사 중 가장 비싼 항목'`
- NOOP 응답: 로그 없음 (원본 유지)
- 실패: `WARNING chat.services.query_rewriter: 쿼리 재작성 실패, 원본 사용: <reason>`

TokenUsage 테이블에는 main 호출과는 별개로 `gpt-4o-mini` 레코드가 한 건 더 쌓인다.

---

## 6. 테스트

`chat/tests.py` 를 `chat/tests/` 패키지로 전환하고 `test_query_rewriter.py` 에 5 케이스 추가:

- `test_empty_history_returns_original_without_llm_call` — history=[] 면 LLM 호출조차 하지 않는다.
- `test_noop_sentinel_keeps_original_question` — LLM 이 `NOOP` 을 돌려주면 원본 반환, usage 는 여전히 수집.
- `test_llm_failure_falls_back_to_original` — `QueryPipelineError` 시 원본 + `usage=None`.
- `test_follow_up_uses_rewritten_query` — 정상 경로: 경조사 맥락 + "비싼거" → "경조사 중 가장 비싼 항목".
- `test_llm_output_cleanup_strips_quotes_and_prefix` — `검색어: "연차 일수"` 형태 탈선을 정리.

```
Ran 5 tests in 0.007s
OK
```

---

## 7. 검증

### 자동
- `docker compose exec -T web python manage.py check` 통과
- `docker compose exec -T web python manage.py test chat.tests.test_query_rewriter` 전건 OK

### 수동 (브라우저)
- 세션 A: "경조사 규정 알려줘" → 정상, ChatLog 생성 → "비싼거" → 서버 로그에 `쿼리 재작성: '비싼거' → '...'` + 응답이 **본인 상 500만원** 을 언급
- 세션 B (새 세션): "비싼거" → 재작성 skip (history=[] 경로, 로그 없음), 기존 동작(no-info 또는 엉뚱한 답) 유지
- 자립 질문: "퇴직금 계산식 알려줘" → `NOOP` 응답 → 원본으로 검색

### 회귀 관찰 포인트
- history 빈 첫 질문의 p95 지연 / 토큰이 기존과 동일 (재작성 skip 경로가 진짜 skip 인지)
- OpenAI 재작성 호출 실패가 502 를 만들지 않는지 (fallback 경로가 동작)
- CanonicalQA 캐시 히트율 급락 여부 — 임베딩 기반이라 재작성 결과가 오히려 유리할 것으로 예상. 단기 관찰 필요.

---

## 8. Out of Scope (Phase 5 이후)

- HyDE · multi-query · query expansion-to-N
- 대화 요약 기반 검색 (`summarize(history) → search`)
- Reranker 에 history 주입
- BO 에서 재작성 로그 / 샘플 / 실패 케이스 대시보드
- 재작성 on/off 기능 플래그 (전사 토글)
- workflow / agent 파이프라인에서의 쿼리 재작성 (필요할 때 같은 helper 재사용)
- TokenUsage 레코드에 호출 목적(main / rewriter / reranker) 메타 필드 도입

# 2026-04-23 개발 로그 — 2.0.0 Phase 3: 기존 코드 개편

## 배경
Phase 2 에서 실행 진입점은 LangGraph 위로 올렸지만, single-shot 의 실제 로직은 여전히 `chat/services/query_pipeline.answer_question()` 하나가 다 가지고 있었다. 검색·재정렬·QA 캐시·프롬프트 조립·OpenAI 호출·TokenUsage 저장·ChatLog 저장·분류·sources 구성까지 한 함수 안에 뒤섞인 상태라, Phase 4 (router) 이후에 workflow / agent 가 붙으면 같은 흐름이 곳곳에서 반복될 수밖에 없었다.

Phase 3 의 목표는 **사용자 관점 동작은 그대로 두고, `answer_question()` 을 7개 단계(retrieval / qa_cache / prompting / llm / postprocess / pipeline / types)로 분해해 `chat/services/single_shot/` 패키지로 옮기는 것**. 동시에 Phase 2 의 안전장치였던 `USE_LANGGRAPH_PIPELINE` feature flag 와 `chat/services/query_pipeline.py` 모듈 자체를 제거하고, Phase 5~6 을 위한 `chat/workflows/` 빈 골격을 선배치했다.

---

## 1. 패키지 구조

```
chat/
  graph/                           # Phase 2 에서 만든 실행 구조 (유지)
    app.py
    state.py
    nodes/{router,single_shot}.py
  services/
    single_shot/                   # 이번 phase 의 중심
      __init__.py                  # 공통 규칙 docstring
      types.py                     # QueryResult, QueryPipelineError
      retrieval.py                 # retrieve_documents
      qa_cache.py                  # find_canonical_qa, resolve_cache_hit
      prompting.py                 # build_single_shot_messages (얇은 래퍼)
      llm.py                       # run_chat_completion
      postprocess.py               # classify_reply, record_token_usage,
                                   # persist_chat_log, build_sources
      pipeline.py                  # run_single_shot (조합자)
    prompt_builder.py / prompt_loader.py / prompt_registry.py  # Phase 1
    qa_retriever.py · reranker.py · history_service.py         # 유지
  workflows/                       # Phase 5~6 자리
    __init__.py
    core/__init__.py
    domains/__init__.py
```

`chat/services/query_pipeline.py` 는 이 PR 안에서 **삭제**되었다. 중간 커밋(Step 1~6)에서는 shim 으로 유지하다가 Step 8 에서 지웠다.

---

## 2. 공통 규칙 (services/single_shot/`__init__.py`)

Phase 5~7 에서도 같은 기준으로 동작해야 할 원칙을 파일 docstring 으로 고정:

1. **history** — view 에서만 load/save. 서비스·graph 는 read-only 파라미터로만.
2. **error** — 서비스 내부는 `QueryPipelineError` 만 raise. graph 노드에서 `state.error` 로 포획, view 가 502 로 변환. 다른 예외 타입은 Django 기본 500 으로.
3. **token usage** — `postprocess.record_token_usage` 에서만 기록. 캐시 히트 경로(OpenAI 호출 없음) 에서는 기록하지 않음.
4. **chat log** — `postprocess.persist_chat_log` 에서만 저장. 캐시 히트 시 저장하지 않음.
5. **sources** — `postprocess.build_sources` 에서만 구성, document_id 기준 중복 제거.
6. **분류** — `postprocess.classify_reply` 한 곳에서 (is_no_info, is_casual) 판정. 마커 상수도 같은 모듈에만 둠.

---

## 3. helper 시그니처

| helper | 입력 | 반환 |
|---|---|---|
| `retrieval.retrieve_documents` | `question: str` | `list[ChunkHit]` (rerank 완료) |
| `qa_cache.find_canonical_qa` | `question: str` | `list[QAHit]` |
| `qa_cache.resolve_cache_hit` | `qa_hits` | `Optional[QueryResult]` (히트 시 완성 결과) |
| `prompting.build_single_shot_messages` | `question, chunk_hits, qa_hits, history` | `list[dict]` (OpenAI messages) |
| `llm.run_chat_completion` | `messages` | `(reply: str, usage, model: str)` |
| `postprocess.classify_reply` | `reply` | `(is_no_info: bool, is_casual: bool)` |
| `postprocess.record_token_usage` | `model, usage` | `None` (DB 저장) |
| `postprocess.persist_chat_log` | `question, reply, chunk_hits` | `Optional[int]` (chat_log_id) |
| `postprocess.build_sources` | `chunk_hits` | `list[dict]` |
| `pipeline.run_single_shot` | `question, history` | `QueryResult` |

`pipeline.py` 가 조합자. 다른 helper 들은 서로 직접 import 하지 않고 `types.py` 만 공유한다 (순환 import 회피).

---

## 4. flag 제거

- `AI_Chat/settings.py` — `USE_LANGGRAPH_PIPELINE` 설정 삭제
- `env.example.txt` / `env.prod.example.txt` — Phase 2 override 주석 블록 삭제
- `chat/views/message.py` — `if settings.USE_LANGGRAPH_PIPELINE:` 분기 삭제. 이제 항상 `run_chat_graph` 호출
- `chat/services/query_pipeline.py` — 모듈 자체 삭제 (잔여 참조 0 건 grep 으로 확인)

운영 환경에서 회귀 발견 시 롤백 수단은 이제 "develop 의 Phase 3 커밋을 revert" 뿐이다. Phase 2 에서 flag 로 제공하던 즉시 fallback 은 없어졌다. 대신 Phase 2 동안 dev/운영 모두 graph 경로를 이미 검증한 상태라 실질 위험은 낮다.

---

## 5. 커밋 구성 (총 11개)

```
Step 0  docs: Save Phase 3 design and detailed plan
Step 1  refactor: Introduce single_shot package with shared types
Step 2  refactor: Extract retrieval helper into single_shot/retrieval
Step 3  refactor: Extract qa_cache helper into single_shot/qa_cache
Step 4  refactor: Extract prompting and llm helpers into single_shot/
Step 5  refactor: Extract post-processing helpers into single_shot/postprocess
Step 6  refactor: Compose run_single_shot pipeline from extracted helpers
Step 7  refactor: Point chat graph at single_shot.pipeline directly
Step 8  refactor: Remove USE_LANGGRAPH_PIPELINE flag and legacy query_pipeline module
Step 9  chore: Add workflows/ skeleton for Phase 5–6
Step 10 docs: Document Phase 3 refactor and common rules
```

각 커밋 직후 `python manage.py check` 와 `POST /message/` 회귀를 통과시켰다 — 점진 치환이라 중간 상태에서도 서비스는 항상 green.

---

## 6. 검증

### 자동 smoke (매 커밋 후 / 최종 커밋 후)
- `docker compose exec web python manage.py check` → OK
- `from chat.services.single_shot.pipeline import run_single_shot; run_single_shot('테스트', [])` → `QueryResult`
- `grep -rn "chat.services.query_pipeline" chat/ bo/ AI_Chat/ --include='*.py'` → 0 건
- `grep -rn "USE_LANGGRAPH_PIPELINE" chat/ bo/ AI_Chat/ --include='*.py'` → 0 건
- Django test client `POST /message/` → 200, body keys `['reply','sources','chat_log_id']`

### 브라우저 회귀
- [ ] 자료 있는 질문 → 출처 배지·피드백 버튼 동작
- [ ] 자료 없는 질문 → "회사 자료에 해당 정보가 없습니다", 출처·피드백 숨김
- [ ] 잡담성 짧은 질문 → casual 분류로 출처·피드백 숨김
- [ ] 다중 턴 대화 → history 반영 유지
- [ ] `[초기화]` 버튼 → 세션 리셋

### 회귀 민감 포인트
- QA 캐시 히트 시 ChatLog / TokenUsage **저장 안 함** (기존 규칙 유지)
- no-info / casual 응답 시 `sources=[]`, `chat_log_id=None`
- `_CASUAL_MAX_LEN=80` 유지
- `temperature=0` 유지 (`llm.run_chat_completion` 내부)
- OpenAI 실패 시 `QueryPipelineError` → view 502

---

## 7. 리스크 대응 기록

| 리스크 | 대응 |
|---|---|
| 중간 커밋에서 import 경로가 일시적으로 어긋남 | Step 1 에서 shim 을 만들어 하위 호환 유지. Step 7 에서 graph 를 먼저 새 경로로 옮긴 뒤 Step 8 에서 shim 삭제. 모든 중간 커밋에서 `manage.py check` 통과 확인 |
| helper 분리 중 side effect 순서·중복 | `record_token_usage` / `persist_chat_log` / `build_sources` 를 `postprocess.py` 에 몰아두고 `pipeline.run_single_shot` 에서 한 줄 호출로만 엮음. 분리 전후 요청을 3 케이스(자료 있음 / 없음 / 잡담)로 각각 실행해 동일 결과 확인 |
| helper 간 순환 import | `types.py` 를 최하단. 다른 helper 는 types 만 import, 서로 직접 import 금지. `pipeline.py` 가 단일 조합자 |
| feature flag 제거 후 롤백 경로 상실 | Phase 2 동안 이미 graph 경로로 운영 검증 완료. 회귀 발견 시 `git revert` 로 Phase 3 커밋만 되돌리면 Phase 2 상태로 복귀 가능 |
| 파일 수만 늘고 실제 가독성 저하 | 설계 §13-1 "역할이 분명할 때만 분리" 원칙. `prompting.py` 는 얇은 래퍼 (20 줄), 나머지는 50~80 줄 수준 |

---

## 8. 변경 파일 요약

### 신규 (12)
- `chat/services/single_shot/__init__.py` (공통 규칙 docstring)
- `chat/services/single_shot/types.py`
- `chat/services/single_shot/retrieval.py`
- `chat/services/single_shot/qa_cache.py`
- `chat/services/single_shot/prompting.py`
- `chat/services/single_shot/llm.py`
- `chat/services/single_shot/postprocess.py`
- `chat/services/single_shot/pipeline.py`
- `chat/workflows/__init__.py` / `core/__init__.py` / `domains/__init__.py`
- `resources/documents/2026-04-23-phase3-refactor.md` (본 문서)
- `resources/plans/detail/2.0.0_Phase 3_기존_코드_개편_개발_플랜.md`
- `resources/plans/2.0.0_Phase 3_기존_코드_개편_개발_설계.md` (디스크에만 있던 걸 체크인)

### 수정 (5)
- `chat/graph/state.py` · `chat/graph/app.py` · `chat/graph/nodes/single_shot.py` — types 경로 및 `run_single_shot` 직접 호출
- `chat/views/message.py` — flag 분기 제거, graph 경로 단일화, import 경로 정리
- `AI_Chat/settings.py` — `USE_LANGGRAPH_PIPELINE` 삭제
- `env.example.txt` / `env.prod.example.txt` — flag 주석 블록 삭제
- `README.md` — 섹션 3-1 서비스 레이어 재정리 + 섹션 11 개발 로그 행 추가

### 삭제 (1)
- `chat/services/query_pipeline.py`

---

## 9. Phase 4 로 넘길 것

- `chat/graph/nodes/router.py` 에 실제 분기 로직 (규칙 기반 1차 판정 + 필요 시 저비용 모델 보조)
- `GraphState` 의 `route` 값 확장 (`'single_shot'` 외에 `'workflow'`, `'agent'` 추가)
- `graph/app.py` 의 `add_conditional_edges` 매핑에 workflow/agent 노드 연결 (Phase 5~7 에서)
- 공통 규칙 docstring 을 workflow/agent 전용 helper 가 지키는지 PR 리뷰로 확인

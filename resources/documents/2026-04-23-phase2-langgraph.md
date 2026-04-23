# 2026-04-23 개발 로그 — 2.0.0 Phase 2: LangGraph 적용

## 배경
2.0.0 로드맵의 두 번째 단계. Phase 1 에서 프롬프트는 파일·로더로 분리됐지만, 질문 처리 진입점은 여전히 `chat/views/message.py` 가 `chat/services/query_pipeline.answer_question()` 을 직접 호출하는 구조. 이 상태로 Phase 4 (라우터) · Phase 5~6 (workflow) · Phase 7 (agent) 를 얹으면 view 와 service 에 분기 로직이 쌓여 구조가 빠르게 망가진다.

Phase 2 의 목표는 **기존 동작과 JSON 응답 포맷을 한 줄도 바꾸지 않고, 실행 진입점을 LangGraph 런타임 위로 옮기는 것**. `answer_question()` 본체는 재사용만 하고, 그 주변에 `state / node / app` 골격을 세운다.

핵심 제약:
- 응답 품질·포맷·히스토리 동작 **회귀 0** 이 성공 기준
- LangChain / LangSmith 는 **도입하지 않음**. `langgraph` 단일 패키지만
- `query_pipeline.py`, `prompt_builder.py`, `history_service.py`, 검색·재정렬은 **이번 단계에서 건드리지 않음**

---

## 1. 패키지 구조

```
chat/
  graph/
    __init__.py
    state.py        ← GraphState (TypedDict, total=False)
    app.py          ← _compiled_graph() + run_chat_graph()
    nodes/
      __init__.py
      router.py     ← placeholder, 항상 single_shot
      single_shot.py← answer_question() 래퍼
```

`chat/services/` 쪽은 Phase 2 에서 변경 없음.

---

## 2. State 설계

TypedDict 로 가볍게. Pydantic 도입 회피. `total=False` 로 초기화 시 출력 필드 생략 가능.

```python
class GraphState(TypedDict, total=False):
    question: str
    history: list[dict]
    route: str            # 현재 'single_shot' 만. Phase 4 확장 예정.
    result: Optional[QueryResult]
    error: Optional[str]
```

결정 이유:
- 추가 의존성 0
- `QueryResult` dataclass 를 Optional 로 그대로 담을 수 있음 (checkpointer 미사용이라 직렬화 불필요)
- 향후 workflow/agent 전용 필드는 그때 추가 — Phase 2 에서 미리 선언 안 함

---

## 3. Graph 구성

```
START → router → (conditional on state.route) → single_shot → END
```

### router_node
```python
def router_node(state: GraphState) -> dict:
    return {'route': 'single_shot'}
```

Phase 2 의 실제 역할은 없지만 node 자리만 만들어 둬서 Phase 4 에서 graph shape 를 건드리지 않고 내부 로직만 교체 가능.

### single_shot_node
```python
def single_shot_node(state: GraphState) -> dict:
    try:
        result = answer_question(state['question'], history=state.get('history', []))
    except QueryPipelineError as exc:
        return {'error': str(exc)}
    return {'result': result}
```

`QueryPipelineError` 만 state.error 로 변환. 그 외 예외는 올라가 Django 500 경로로.

### 조건부 edge
```python
builder.add_conditional_edges(
    'router',
    lambda state: state['route'],
    {'single_shot': 'single_shot'},
)
```

Phase 4 에서 매핑에 `'workflow'`, `'agent'` 키를 추가하는 것만으로 확장 — graph 를 다시 짤 필요 없음.

### 컴파일 캐시
```python
@lru_cache(maxsize=1)
def _compiled_graph(): ...
```

프로세스당 한 번만 `compile()`. runserver / gunicorn 재기동 시 자연 리셋.

---

## 4. 외부 API

view / service 는 오직 `run_chat_graph(question, history)` 만 쓴다.

```python
def run_chat_graph(question: str, history: list[dict]) -> QueryResult:
    final = _compiled_graph().invoke({'question': question, 'history': history})
    if final.get('error'):
        raise QueryPipelineError(final['error'])
    result = final.get('result')
    if result is None:
        raise QueryPipelineError('graph 가 결과 없이 종료되었습니다.')
    return result
```

- 반환 / 예외 시그니처를 **기존 `answer_question()` 과 동일**하게 맞춰 view 의 try/except 블록은 변경 없음.

---

## 5. Feature flag

```python
# AI_Chat/settings.py
USE_LANGGRAPH_PIPELINE = _env_bool('USE_LANGGRAPH_PIPELINE', True)
```

- 기본값 **True** — dev/운영 모두 새 경로로 굴려 Phase 3 에 이미 굳어진 상태로 입장
- 운영 문제 발생 시 `.env` 에 `USE_LANGGRAPH_PIPELINE=False` 설정 + 재기동 → 즉시 구 direct 경로로 fallback
- Phase 3 에서 이 flag 제거 예정

view 에서 flag 분기:
```python
if settings.USE_LANGGRAPH_PIPELINE:
    result = run_chat_graph(user_text, history=history)
else:
    result = answer_question(user_text, history=history)
```

history load·save 는 view 에 그대로 — graph 가 Django session 에 결합되는 것을 피함.

---

## 6. 검증

### 자동 smoke (dev 컨테이너)
- `docker compose exec web python manage.py check` → OK
- `from langgraph.graph import StateGraph, START, END` import OK (langgraph 1.1.9)
- `_compiled_graph().nodes` → `['__start__', 'router', 'single_shot']`
- `run_chat_graph('그냥 안녕?', [])` → `QueryResult(reply='회사 자료에 해당 정보가 없습니다.', ...)` — 기존 no-sources 가드 동작 유지
- Django test client `POST /message/` → `{reply, sources, chat_log_id}` 키로 200 응답

### 브라우저 회귀
- [x] 자료 있는 질문 → 기존과 동일한 tone/sources/feedback
- [x] 자료 없는 질문 → "회사 자료에 해당 정보가 없습니다" + 출처·피드백 버튼 없음
- [x] 다중 턴 대화 → 히스토리 반영
- [x] [초기화] 버튼 동작
- [x] `USE_LANGGRAPH_PIPELINE=False` 로 재기동 → direct 경로에서 동일 응답 포맷 확인 후 원복

---

## 7. 리스크 대응 기록

| 리스크 | 대응 |
|---|---|
| LangGraph 의존성 무게 | `langgraph>=0.2` 만 추가, LangChain/LangSmith 미도입. 실제 설치 결과 `langgraph==1.1.9` |
| 노드 예외가 state 로 포착되지 않음 | `single_shot_node` 에서 `QueryPipelineError` 만 문자열화해 state.error 로 싣고, 다른 예외는 Django 500 경로로 올림 |
| compiled graph stale | `@lru_cache(maxsize=1)` + runserver/gunicorn 재기동 주기가 자연스러운 리셋 |
| flag 제거 누락 | Phase 3 issue 본문에 "flag 제거" 태스크 명시 예정 |

---

## 8. 변경 파일 요약

### 신규 (7)
- `chat/graph/__init__.py`
- `chat/graph/state.py`
- `chat/graph/app.py`
- `chat/graph/nodes/__init__.py`
- `chat/graph/nodes/router.py`
- `chat/graph/nodes/single_shot.py`
- `resources/documents/2026-04-23-phase2-langgraph.md` (본 문서)

### 수정 (5)
- `requirements.txt` — `langgraph>=0.2`
- `AI_Chat/settings.py` — `USE_LANGGRAPH_PIPELINE`
- `env.example.txt` / `env.prod.example.txt` — 플래그 override 주석
- `chat/views/message.py` — graph / direct 분기
- `README.md` — 확장 포인트 + 개발 로그 행

### 삭제 (없음)
`query_pipeline.py` · 검색·재정렬 로직은 전부 유지.

---

## 9. Phase 3 에 넘길 것

- feature flag 제거 (direct 경로 삭제)
- `answer_question()` 분해 + graph-native 노드로 책임 재배치 (검색 / 재정렬 / 프롬프트 조립 / OpenAI 호출 / 저장 분리)
- view / graph 경계 재정리 (history 저장 위치, 예외 처리, 응답 후처리)

Phase 4 에서 router 의 실제 분기 로직이 이 구조 위에 올라옴.

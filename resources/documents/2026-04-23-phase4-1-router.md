# 2026-04-23 개발 로그 — 2.0.0 Phase 4-1: 질문 라우팅 Core

## 배경
Phase 2~3 에서 graph 진입점과 single_shot 내부 구조는 정리됐지만, `chat/graph/nodes/router.py` 는 여전히 `{'route': 'single_shot'}` 만 반환하는 placeholder 였다. 모든 요청이 single-shot 으로만 흘러 Phase 5~7 (workflow / agent) 이 붙을 길이 없다.

Phase 4-1 의 목표는 **규칙 기반 router 함수를 구현해 `single_shot / workflow / agent` 3분류를 실제로 결정하게 하는 것**. workflow·agent 노드는 아직 없으므로 conditional edge 에서 세 route 모두 `single_shot` 으로 내부 포워딩한다. 사용자 체감은 기존과 동일, `state.route` 와 서버 로그에만 의도된 경로가 남는다.

Phase 4-2 (BO RouterRule 관리)는 별도 PR.

---

## 1. 패키지 구조

```
chat/
  graph/
    routes.py                      # ROUTE_SINGLE_SHOT / ROUTE_WORKFLOW / ROUTE_AGENT (신규)
    state.py                       # route_reason / matched_rules 필드 추가
    app.py                         # conditional edge 3 route 매핑
    nodes/
      router.py                    # route_question 호출로 교체
      single_shot.py               # 유지
  services/
    question_router.py             # 규칙 기반 분류 (신규)
    single_shot/                   # Phase 3 구조 그대로
    ...
```

---

## 2. 분류 규칙

`chat/services/question_router.py` 에 두 키워드 튜플 + `route_question(question)`:

```python
WORKFLOW_KEYWORDS = (
    '계산', '산정', '얼마', '몇 일', '몇 년', '평균', '합계', '차감',
    '근속', '퇴직금', '연차 계산', '잔여 연차', '급여', '수당',
    '입사일', '퇴사일', '최근 3개월',
)
AGENT_KEYWORDS = (
    '비교', '추천', '유리', '불리', '종합', '예외', '케이스',
    '해석', '충돌', '만약', '내 상황', '어떤 게 나아',
)
```

우선순위: workflow 매치 → agent 매치 → 기본 single_shot. 이유 (설계 §4-1-3):
- workflow 는 정형 계산이라 안정·저렴. 애매할 때 안전.
- agent 는 비용·불확실성이 커 마지막 수단.

반환값은 `RouteDecision(route, reason, matched_rules)` dataclass. 향후 `confidence` 같은 추가 필드는 Phase 4-2 이후 도입.

---

## 3. Graph 변경

**`chat/graph/state.py`** — flat 필드 2개 추가:
```python
route: str
route_reason: str           # 'workflow_keyword' / 'agent_keyword' / 'default'
matched_rules: list[str]    # 매치된 키워드들
```

**`chat/graph/nodes/router.py`** — 실제 분류 호출 + fallback 로깅:
```python
decision = route_question(state['question'])
if decision.route != ROUTE_SINGLE_SHOT:
    logger.info('라우팅: %s (reason=%s, rules=%s) — Phase 5~7 대기 중이라 single_shot 로 포워딩',
                decision.route, decision.reason, decision.matched_rules)
return {'route': decision.route, 'route_reason': decision.reason,
        'matched_rules': list(decision.matched_rules)}
```

**`chat/graph/app.py`** — 3 route 전부 single_shot 노드 매핑:
```python
{
    ROUTE_SINGLE_SHOT: 'single_shot',
    ROUTE_WORKFLOW:    'single_shot',   # Phase 5~6 에서 교체
    ROUTE_AGENT:       'single_shot',   # Phase 7 에서 교체
}
```

Phase 5~7 이 edge 매핑의 `ROUTE_WORKFLOW` / `ROUTE_AGENT` 값을 실제 노드 이름으로 한 줄씩 바꾸고, router_node 의 fallback 로그 조건을 제거하면 된다.

---

## 4. 분류 검증 (실제 shell 출력)

설계 §6 의 예시 + 잡담·no-source 경계 질문으로 smoke.

| 질문 | actual route | reason | matched_rules | 설계 §6 기대 |
|---|---|---|---|---|
| `연차 규정이 뭐야?` | `single_shot` | `default` | `[]` | single_shot ✓ |
| `경조사 휴가는 며칠이야?` | `single_shot` | `default` | `[]` | single_shot ✓ |
| `복지포인트 사용 기준 알려줘` | `single_shot` | `default` | `[]` | single_shot ✓ |
| `퇴직금 얼마야?` | `workflow` | `workflow_keyword` | `['얼마', '퇴직금']` | workflow ✓ |
| `입사일 기준으로 근속연수 계산해줘` | `workflow` | `workflow_keyword` | `['계산', '근속', '입사일']` | workflow ✓ |
| `최근 3개월 급여 평균 내줘` | `workflow` | `workflow_keyword` | `['평균', '급여', '최근 3개월']` | workflow ✓ |
| `올해 내 연차 며칠이야?` | `single_shot` | `default` | `[]` | **workflow** ✗ (아래 주석) |
| `육아휴직이랑 단축근무 중 뭐가 더 유리해?` | `agent` | `agent_keyword` | `['유리']` | agent ✓ |
| `규정 A와 B가 충돌하는 것 같은데 어떻게 해석해?` | `agent` | `agent_keyword` | `['해석', '충돌']` | agent ✓ |
| `내 상황에 맞는 제도 추천해줘` | `agent` | `agent_keyword` | `['추천', '내 상황']` | agent ✓ |
| `안녕` | `single_shot` | `default` | `[]` | (잡담 — single_shot) ✓ |
| `오늘 점심 뭐 먹지?` | `single_shot` | `default` | `[]` | (no-sources — single_shot) ✓ |

### 알려진 오분류 사례
- **"올해 내 연차 며칠이야?"** — 설계 §6 기대는 workflow, 실제는 single_shot. 원인: 설계 §4-1-4 키워드에 `몇 일` 은 있지만 `며칠` 은 없음. Phase 4-1 은 설계 키워드 리스트에 충실하게 구현했기 때문.
  - 대응: Phase 4-2 에서 BO RouterRule 로 `며칠` 을 workflow 패턴에 추가하거나, WORKFLOW_KEYWORDS 상수에 직접 보강. BO 운영 조정의 대표 사례로 기록.

### 그래프 end-to-end (응답 JSON)
POST `/message/` 가 route 별로 동일한 `{reply, sources, chat_log_id}` 키를 돌려주는지 확인:
- `연차 규정이 뭐야?` (single_shot) → 200, 실제 자료 기반 답변
- `퇴직금 얼마야?` (workflow → single_shot fallback) → 200, "자료에 명시되지 않음"
- `육아휴직이랑 단축근무 중 뭐가 더 유리해?` (agent → single_shot fallback) → 200, 자료 기반 비교 답변

---

## 5. 설계 결정 기록

### 왜 keyword substring 매칭인가
- regex / NLP 는 오버엔지니어링. Phase 4-1 범위는 "3분류 구조를 살리기"뿐.
- 오분류 위험은 알고 있고, Phase 4-2 의 BO rule priority + negative pattern 으로 조정할 예정.

### 왜 내부 포워딩 fallback 인가
- 선택지: (a) 내부 포워딩으로 사용자 체감 동일, (b) placeholder 노드가 "아직 준비 중" 응답.
- (a) 선택 이유: Phase 4-1 은 UX 퇴행 없이 구조만 까는 게 목적. 사용자가 "퇴직금 얼마야?" 를 물었을 때 "준비 중" 만 돌려주면 현재보다 나빠짐.
- (a) 는 Phase 5 PR 이 edge 매핑 한 줄만 고치면 open 된다는 부수 효과도 있음.

### 왜 `route_reason` / `matched_rules` 를 flat 필드로 두는가
- TypedDict 에 중첩 객체 담으면 LangGraph state merge 가 꼬일 수 있음.
- 서버 로그·Phase 4-2 BO 관측에서 그대로 꺼내 쓰기 편함.
- `single_shot/__init__.py` 의 "state 는 단순 dict" 원칙과 일관.

---

## 6. 커밋 구성

```
Step 0  docs: Save Phase 4 design and Phase 4-1 detailed plan
Step 1  feat: Add route constants module for chat graph
Step 2  feat: Add rule-based question_router with workflow/agent keywords
Step 3  feat: Extend GraphState with route_reason and matched_rules
Step 4  refactor: Wire router_node to rule-based question_router
Step 5  feat: Route workflow/agent intents through single_shot until later phases land
Step 6  docs: Document Phase 4-1 router with verified example classifications
Step 7  feat: Configure app logging so INFO messages reach the console
```

각 커밋 직후 `manage.py check` + graph 호출 smoke 통과.

### Step 7 보강 — 왜 LOGGING 을 건드렸나
router_node 의 fallback INFO 로그가 실제로 찍히지 않는 문제가 최종 브라우저 검증에서 드러났다. Django 기본 LOGGING 은 `django.*` 로거만 콘솔로 보내고, 우리 앱 로거(`chat.*` / `bo.*` / `files.*`)는 root 로 올라가 WARNING 이상만 통과. 결과적으로 Phase 4-1 의 observability 약속(servers log 에서 의도된 route 확인)이 거짓말이 되는 상태였다.

`AI_Chat/settings.py` 에 최소 LOGGING dict 를 추가해 3 앱의 INFO 를 console handler 로 보낸다. 포매터는 `'{levelname} {name}: {message}'`. 부수 효과로 Phase 3 에서 추가했던 `chat.services.single_shot.retrieval` / `qa_cache` 의 `logger.info` 라인도 이제 `docker compose logs web` 에서 보인다.

---

## 7. Phase 4-2 / Phase 5 로 넘길 것

**Phase 4-2 (BO RouterRule 관리)**
- `RouterRule` 모델 (name / route / match_type / pattern / priority / enabled / description)
- BO CRUD 화면 (`/bo/router-rules/`)
- `route_question` 안에서 DB rule 우선 → 본 모듈의 키워드 상수 fallback 순서
- `며칠` 같은 누락 키워드를 BO 에서 보강하는 동선 검증

**Phase 5 / Phase 6 / Phase 7**
- 실제 workflow / agent 노드 구현
- `chat/graph/app.py` 의 conditional edge 매핑:
  - `ROUTE_WORKFLOW: 'workflow'` (Phase 5~6)
  - `ROUTE_AGENT: 'agent'` (Phase 7)
- `chat/graph/nodes/router.py` 의 "Phase 5~7 대기 중이라 single_shot 로 포워딩" INFO 로그 제거
- workflow/agent 노드 내부도 `chat/services/single_shot/__init__.py` 의 공통 규칙 (history / error / token / ChatLog / sources) 을 지키는지 PR 리뷰 필수

---

## 8. 변경 파일 요약

### 신규 (4)
- `chat/graph/routes.py`
- `chat/services/question_router.py`
- `resources/plans/detail/2.0.0_Phase 4-1_질문_라우팅_Core_개발_플랜.md`
- `resources/documents/2026-04-23-phase4-1-router.md` (본 문서)

Step 0 에서 `resources/plans/2.0.0_Phase 4_질문_라우팅_개발_설계.md` 도 디스크에만 있던 걸 체크인.

### 수정 (3)
- `chat/graph/state.py` — `route_reason`, `matched_rules` 필드
- `chat/graph/nodes/router.py` — `route_question` 호출 + fallback 로깅
- `chat/graph/app.py` — conditional edge 3 route 매핑
- `README.md` — 서비스 레이어 설명 + 개발 로그 행

### 삭제 (없음)

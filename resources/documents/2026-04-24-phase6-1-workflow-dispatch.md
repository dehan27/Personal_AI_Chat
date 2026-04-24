# 2026-04-24 개발 로그 — 2.0.0 Phase 6-1: Workflow Dispatch 인프라

## 배경

Phase 5 에서 `chat/workflows/core/` 를 6 모듈로 완성해 뒀지만, 실제 사용처가 없는 라이브러리 상태였다. Phase 4 라우팅은 질문을 `single_shot / workflow / agent` 로 분류했지만 `workflow` 와 `agent` 는 여전히 single_shot 으로 포워딩됐다.

Phase 6 설계(`resources/plans/2.0.0_Phase 6_범용_Workflow_Domain_개발_설계.md`)는 회사 전용 계산(퇴직금·연차)이 아닌 **질문 유형(`date_calculation`, `amount_calculation`, `table_lookup`, …)** 중심의 generic workflow 를 올리는 것을 목표로 한다. 분량이 커서 **6-1 / 6-2 / 6-3** 세 단계로 쪼갠다:

- **6-1 (이번)** — dispatch 인프라 + 첫 generic workflow(`date_calculation`).
- **6-2** — `amount_calculation` + 자연어에서 workflow_input 추출.
- **6-3** — `table_lookup` + 남은 정책 다듬기.

Phase 6-1 의 핵심 약속은 **회귀 0**: 운영자가 BO RouterRule 에 `workflow_key` 를 명시적으로 지정하지 않는 한 graph 는 예전처럼 single_shot 으로 답한다.

---

## 1. 패키지 구조

```
chat/workflows/domains/
  __init__.py                 # registry/dispatch 재노출 + general import 부작용
  registry.py                 # WorkflowEntry + register / get / has / all_entries
  dispatch.py                 # run(workflow_key, raw) 진입점
  reply.py                    # WorkflowResult → 사용자 응답 문자열
  general/
    __init__.py               # date_calculation import 부작용
    date_calculation.py       # 첫 generic workflow

chat/graph/
  nodes/workflow.py           # 신규 — dispatch 또는 single_shot 폴백
  nodes/router.py             # workflow_key 를 state 에 실음
  nodes/single_shot.py        # 변경 없음
  app.py                      # ROUTE_WORKFLOW edge 를 workflow 노드로 재연결
  state.py                    # GraphState 에 workflow_key 필드
```

의존 방향은 Phase 5 원칙을 이어받는다:

```
core (result / validation / dates / numbers / formatting / base)
   ↑
domains (registry / dispatch / reply / general/*)
   ↑
graph (nodes/workflow)
```

`registry.py` 는 다른 core/도메인 모듈을 import 하지 않고 `BaseWorkflow` 타입만 참조 — 순환 차단.

---

## 2. 라우팅 결정 확장

```python
@dataclass(frozen=True)
class RouteDecision:
    route: str
    reason: str
    matched_rules: List[str] = field(default_factory=list)
    workflow_key: str = ''          # Phase 6-1
```

- DB `RouterRule` 에서 규칙이 매치될 때 `workflow_key` 가 같이 실려 온다.
  단, 안전장치로 `rule.route != 'workflow'` 인 경우는 key 를 버리고 빈 값 반환.
- 코드 키워드 fallback 경로(`WORKFLOW_KEYWORDS` / `AGENT_KEYWORDS`)는 **항상 `workflow_key=''`**. 자동 추론 없음 → Phase 4-1 동작 그대로.

`GraphState` TypedDict 에도 동일 필드 추가. 기존 state 소비자는 이 필드를 몰라도 무방.

---

## 3. RouterRule 스키마 변경

- 새 컬럼: `workflow_key = CharField(max_length=64, blank=True, default='')`.
- 마이그레이션: `chat/migrations/0010_routerrule_workflow_key.py` (`AddField`).
- 기존 rule 은 전부 빈 값으로 채워져 behaviour 동일.
- BO 폼에 `ChoiceField` (`_workflow_key_choices()` 로 registry 기반 동적 옵션) 추가. 첫 옵션 "— 선택 안 함 (single_shot 으로 폴백) —" 이 빈 값.

---

## 4. Registry + Dispatch

**registry.py** 는 프로세스 싱글톤 `dict[str, WorkflowEntry]` + `register / get / has / all_entries + _snapshot/_restore/_reset_for_tests`. 중복 key · 빈 key 는 즉시 `ValueError`.

```python
@dataclass(frozen=True)
class WorkflowEntry:
    key: str
    title: str
    description: str
    status: str                           # 'stable' | 'beta'
    factory: Callable[[], BaseWorkflow]
```

**dispatch.py** 는 진입점이 `run(workflow_key, raw) -> WorkflowResult` 단 하나:

- 빈 key / 미등록 key → `WorkflowResult.unsupported(reason=...)` 그대로 반환.
- 등록된 key → `entry.factory()` 로 인스턴스 만들어 Phase 5 `run_workflow(...)` 에 넘김.
- 도메인 로직은 **절대** 두지 않음.

테스트 격리: registry 는 싱글톤이라 테스트가 `_reset_for_tests()` 를 부르면 다른 테스트 순서에 따라 auto-register 된 entry 까지 날아갈 수 있었다. `_snapshot/_restore` 를 추가해 단위 테스트가 setUp 에서 스냅샷, tearDown 에서 복원하도록 했다.

---

## 5. 첫 generic workflow: `date_calculation`

입력 스펙 (`Mapping[str, Any]`):
- `start: str` — 시작 날짜 (core `parse_date` 포맷)
- `end: str` — 종료 날짜 (동일)
- `unit: str` — `'days' | 'months' | 'years'` (기본 `'days'`)

실행:
1. `require_fields({'start', 'end'})` → 부족 시 `MISSING_INPUT`.
2. unit 검증 (지원 외 값) → `INVALID_INPUT`.
3. `ensure_date_order(start, end)` → `INVALID_INPUT` (파싱 실패 · 역순 모두 수집).
4. `unit` 에 따라 `days_between / months_between / years_between`.
5. `WorkflowResult.ok(value=<int>, details={start, end, unit, unit_label})`.

LLM 호출 없음. 모든 계산이 Phase 5 core 의 순수 함수 조합.

모듈 import 시점에 `registry.register(...)` 로 자신을 등록한다. `general/__init__.py` 가 이 import 를 트리거 → `chat.workflows.domains` 가 import 되는 순간 자동 등록.

---

## 6. Reply 포맷터

`chat/workflows/domains/reply.py::build_reply_from_result(result, *, workflow_key)` 가 `WorkflowStatus` 4 개 값에 맞춰 한국어 응답을 조립한다.

- **OK** — key 별 포맷터(`_ok_formatters[key]`). 현재 `date_calculation` 하나. 없는 key 는 fallback("`결과: {value}`"). Phase 6-2/6-3 에서 새 workflow 를 추가할 때 map 에 함수 하나만 더하면 됨.
- **MISSING_INPUT** — "계산하려면 {fields} 정보가 필요합니다."
- **INVALID_INPUT** — `details['errors']` 를 `-` 불릿 리스트로.
- **UNSUPPORTED** — `details['reason']` 가 있으면 같이 노출.

---

## 7. graph workflow_node

```python
def workflow_node(state):
    key = (state.get('workflow_key') or '').strip()
    if not key or not registry.has(key):
        return single_shot_node(state)                 # 폴백 → 기존 경로
    result = dispatch.run(key, state.get('workflow_input') or {})
    return {'result': QueryResult(
        reply=build_reply_from_result(result, workflow_key=key),
        sources=[], total_tokens=0, chat_log_id=None,
    )}
```

`chat/graph/app.py` 는 `ROUTE_WORKFLOW` conditional edge 를 `'single_shot'` → `'workflow'` 로 교체. `workflow → END`. `ROUTE_AGENT` 는 여전히 `'single_shot'` (Phase 7 대기).

**주의**: 현재 `state.workflow_input` 은 어디서도 채워지지 않는다. 즉 실제 채팅에서 "며칠이야?" 같은 질문이 `date_calculation` 로 라우팅되면 `MISSING_INPUT` → "계산하려면 start, end 정보가 필요합니다." 응답으로 수렴. 자연어에서 `start`/`end` 자동 추출은 **Phase 6-2 의 과제**. Phase 6-1 은 **인프라 자체** 가 증명되는 시점.

---

## 8. 테스트

신규 11 케이스(생성) — `chat/tests/`:
- `test_workflow_registry.py` — 5 (register / lookup / duplicate / empty key / 순서 보존)
- `test_workflow_dispatch.py` — 5 (happy / missing 전달 / unknown / empty key / whitespace)
- `test_workflow_date_calculation.py` — 10 (days/months/years / 한국어 자연어 / missing / blank / 형식 오류 / 역순 / unit / auto-register)
- `test_workflow_reply.py` — 7 (각 status + fallback)
- `test_workflow_node.py` — 4 (fallback 2 + registered 2)

```
$ docker compose exec -T web python manage.py test chat.tests
Ran 145 tests in 0.027s   (Phase 5 114 + Phase 6-1 31)
OK
```

---

## 9. 수동 smoke

```python
>>> from chat.workflows.domains import dispatch, registry
>>> [e.key for e in registry.all_entries()]
['date_calculation']

>>> dispatch.run('date_calculation',
...              {'start': '2025-01-01', 'end': '2025-02-01', 'unit': 'days'})
WorkflowResult(status=<WorkflowStatus.OK>, value=31, details=...)

>>> dispatch.run('unknown', {})
WorkflowResult(status=<WorkflowStatus.UNSUPPORTED>,
               details={'reason': "등록되지 않은 workflow_key 입니다: 'unknown'"})

>>> # BO 에서 며칠→date_calculation rule 등록 후:
>>> from chat.services.question_router import route_question
>>> route_question('올해 내 연차 며칠이야?')
RouteDecision(route='workflow', reason='db_rule:smoke-며칠',
              matched_rules=['며칠'], workflow_key='date_calculation')
```

브라우저에서 `/bo/router-rules/new/` → Workflow 도메인 드롭다운에 "날짜 계산 (date_calculation)" 노출.

---

## 10. 회귀 체크 포인트

- [x] `workflow_key` 미지정 RouterRule 은 graph 가 기존처럼 single_shot 로 전달 (workflow_node 폴백 테스트).
- [x] 코드 키워드 fallback 경로가 항상 `workflow_key=''` 반환 → 실사용 질문의 행동 변화 없음.
- [x] 마이그레이션 0010 이 기존 rule 의 동작을 바꾸지 않음 (`default=''`).
- [x] Phase 5 core 114 케이스 + 신규 31 전건 통과.

---

## 11. Out of Scope (Phase 6-2 이후)

- `amount_calculation`, `table_lookup`, `document_compare`, `conditional_reasoning`, `multi_source_summary` 실제 구현.
- 자연어 질문에서 `workflow_input` 자동 추출 (Phase 6-2 핵심).
- Agent ReAct / tool calling (Phase 7).
- `unsupported` 답변 템플릿 튜닝 (6-3).
- workflow 실행 로그 / 실패 통계를 BO 에 노출.
- 회사 전용 도메인(`domains/company/`).
- LLM 을 끼우는 workflow 변형.

---

## 12. 완료 정의 (Definition of Done) 충족 여부

- [x] `registry / dispatch / reply / general/date_calculation` 구현.
- [x] `RouteDecision`, `GraphState`, `RouterRule` 에 `workflow_key` 추가 + migration 0010.
- [x] graph `workflow_node` 가 dispatch 경유로 동작 · 미등록 key 는 single_shot 폴백.
- [x] BO `RouterRule` 폼에서 workflow_key 선택 · 목록에 key caption 노출.
- [x] 전 테스트 green (145/145).
- [x] README §3-1 / §11 + 이 dev log 반영.
- [x] 회귀 0 (현재 production 규칙들의 체감 변화 없음).

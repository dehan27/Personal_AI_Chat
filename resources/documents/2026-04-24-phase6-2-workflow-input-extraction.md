# 2026-04-24 개발 로그 — 2.0.0 Phase 6-2: Workflow Input Extraction + amount_calculation

## 배경

Phase 6-1 에서 workflow dispatch 인프라는 완성됐지만, 사용자의 자연어 질문을 `workflow_input` 으로 변환해주는 경로가 없어 실사용 흐름에서 항상 `"계산하려면 start, end 정보가 필요합니다."` 로 수렴했다. 인프라는 있었지만 체감 가치 없었음.

Phase 6-2 가 그 갭을 닫는다:

1. **`workflow_input` 자동 추출** — 각 workflow 가 `input_schema` 를 선언하고, extractor 가 질문에서 값을 뽑아낸다. regex 우선, 부족하면 cheap LLM fallback.
2. **`amount_calculation`** — 두 번째 generic workflow (합계/평균/차이). date_calculation 과 함께 실제 체감 답변이 나오는 첫 순간.

이로써 Phase 6 로드맵의 핵심 한 축이 동작한다 — 나머지 workflow 들은 같은 패턴으로 부품만 얹으면 된다.

---

## 1. 패키지 구조

```
chat/
  services/
    workflow_input_extractor.py    # 신규 — regex + LLM hybrid 추출기
  workflows/domains/
    field_spec.py                  # 신규 — FieldSpec dataclass + 타입 enum
    registry.py                    # WorkflowEntry.input_schema 필드 추가
    general/
      date_calculation.py          # INPUT_SCHEMA 선언
      amount_calculation.py        # 신규 — 합계/평균/차이 workflow
  graph/nodes/
    workflow.py                    # extractor 호출 단계 삽입 + token 기록
assets/prompts/chat/
  workflow_input_extractor.md      # 신규 — LLM fallback 프롬프트
```

의존 방향은 Phase 6-1 과 동일 (순환 없음):

```
core → domains (field_spec / registry / dispatch / reply) → general/*
                                                           ↑
services/workflow_input_extractor  ─────┘ (schema 만 읽음, 도메인 모듈 직접 import X)
                                                           ↓
                                       graph/nodes/workflow (extractor + dispatch 연결)
```

---

## 2. FieldSpec — 각 workflow 의 "필요한 값" 선언

```python
@dataclass(frozen=True)
class FieldSpec:
    type: str                         # 'date' | 'number' | 'money' | 'enum' | 'number_list'
    required: bool = True
    aliases: tuple[str, ...] = ()
    default: object | None = None
    enum_values: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
```

- `type` 화이트리스트는 `SUPPORTED_TYPES` 로 고정. 그 외 값은 `__post_init__` 에서 즉시 거부.
- `enum` 타입은 `enum_values` 필수, 그 외 타입은 `enum_values` 금지 (정합성 강제).
- `aliases` 는 LLM fallback 프롬프트에 힌트로 주어 "start" 와 "시작일" 을 같은 필드로 보게 해줌. regex 단계에선 사용 X.

`WorkflowEntry` 는 `input_schema: Mapping[str, FieldSpec]` 을 기본값 빈 dict 로 갖는다 → 기존 workflow 나 스키마 미선언 workflow 도 자동 호환.

---

## 3. Extractor (`chat/services/workflow_input_extractor.py`)

### 처리 순서
1. **money** 필드 후보 추출 (`(\d+)원`). 구간을 기록해 마스킹 대상에 넣음.
2. **number / number_list** — 1의 구간은 공백으로 마스킹한 텍스트에서 검색.
3. **date** — `YYYY-MM-DD` / `YYYY.MM.DD` / `YYYY/MM/DD` / `YYYY년 MM월 DD일` + 2자리 연도. 선언 순서대로 앞에서부터 배정.
4. **enum** — **money + date 구간을 마스킹한 텍스트** 에서 토큰 매칭. 이 처리가 없으면 `2024년 1월` 안의 `년` 이 먼저 잡혀 `years` 가 되는 버그가 있었음 (스모크에서 발견).
5. **default** — `required=False` + `default != None` 인 필드에 채움.
6. 여전히 `required` 미충족 필드가 있으면 **LLM fallback**.

### LLM fallback

- 프롬프트 파일: `assets/prompts/chat/workflow_input_extractor.md` (Phase 1 registry 등록키 `chat-workflow-input-extractor` — BO 편집 가능).
- 호출: Phase 4-3 `query_rewriter` 와 동일한 패턴으로 `run_chat_completion`.
- 입력: schema 요약 + 이미 regex 로 찾은 값 + 최근 4 turn history + 현재 질문.
- 출력: 엄격한 JSON. `_parse_json_object` 가 코드펜스 / 공백 관용적으로 처리.
- 검증: `_merge_llm_output` 에서 스키마 타입별로 필터.
  - enum → `enum_values` key 집합에 있는 값만.
  - number/money → int 변환 가능해야 함.
  - number_list → 리스트 내 int 변환 가능한 것만.
  - date/text → 공백 아닌 문자열.
- 실패 경로 (네트워크 / JSON 오류 / 타입 불일치) → 모두 "regex 결과만 들고 진행".
  사용자 응답은 자연히 MISSING_INPUT 으로 수렴 (`"start 정보가 필요합니다."`).

### TokenUsage

LLM 이 실제로 호출됐을 때만 `usage`/`model` 이 반환되고, `workflow_node` 가
`record_token_usage(model, usage)` 를 부른다. regex 가 다 채워서 LLM 호출을 안 한 경우엔 추가 TokenUsage 레코드 없음.

---

## 4. `date_calculation` 의 `input_schema`

```python
INPUT_SCHEMA = {
    'start': FieldSpec(type='date', required=True,
                       aliases=('start', '시작', '시작일', '부터')),
    'end':   FieldSpec(type='date', required=True,
                       aliases=('end', '종료', '종료일', '끝', '까지')),
    'unit':  FieldSpec(type='enum', required=False, default='days',
                       aliases=('unit', '단위'),
                       enum_values={
                           'days':   ('일', '며칠', 'days'),
                           'months': ('개월', '달', 'months'),
                           'years':  ('년', 'years'),
                       }),
}
```

Phase 6-1 에서 자리만 잡아뒀던 workflow 가 비로소 자연어 질문에서 직접 답한다.

---

## 5. `amount_calculation` workflow

입력:
- `values: list[int]` — regex extractor 가 money 우선, 없으면 일반 숫자로 수집.
- `op: enum` — `sum / average / diff`. 기본 `sum`.

실행 규칙:
- `values` 미제공 → MISSING_INPUT.
- `values` 가 리스트가 아니거나 내부가 `parse_int_like` 실패하면 INVALID_INPUT.
- `op='diff'` 인데 `len(values) < 2` → INVALID_INPUT.
- `op='sum'` → `sum_amounts`.
- `op='average'` → `average_amount` (항상 float).
- `op='diff'` → `max - min`.

응답 포맷터(`reply.py`)는 op 별 조사까지 맞춰 렌더:
- `합계는 6,000 입니다.`
- `평균은 216.67 입니다.`
- `차이는 40 입니다.`

숫자 포맷: int 는 `{:,}`, float 는 `{:,.2f}`.

---

## 6. graph `workflow_node` 통합

```python
def workflow_node(state):
    key = (state.get('workflow_key') or '').strip()
    if not key or not registry.has(key):
        return single_shot_node(state)  # 폴백 유지

    explicit = state.get('workflow_input')
    if explicit is not None:
        workflow_input = dict(explicit)  # 테스트 escape hatch
    else:
        entry = registry.get(key)
        workflow_input, usage, model = extract_workflow_input(
            question=state.get('question') or '',
            history=state.get('history') or [],
            schema=entry.input_schema,
        )
        if usage and model:
            record_token_usage(model, usage)

    result = dispatch.run(key, workflow_input)
    reply  = build_reply_from_result(result, workflow_key=key)
    ...
```

- `state.workflow_input` 이 외부에서 직접 주입되면 extractor 를 스킵 — 단위 테스트 편의 + 향후 agent 가 workflow 를 툴로 호출할 때 값을 직접 건넬 수 있도록 한 대비.

---

## 7. 실측 (수동 smoke)

```
[date default days]  '2025-01-01 부터 2025-02-01 까지 며칠이야?'
  → 2025-01-01 부터 2025-02-01 까지 31일 입니다.

[date months]        '2024년 1월 1일부터 2025년 3월 15일까지 몇 개월이야?'
  → 2024-01-01 부터 2025-03-15 까지 14개월 입니다.

[date years]         '2020-05-10 부터 2025-05-10 까지 몇 년이야?'
  → 2020-05-10 부터 2025-05-10 까지 5년 입니다.

[amount sum]         '1,000원과 2,000원과 3,000원 합계는?'
  → 합계는 6,000 입니다.

[amount average]     '100 200 350 평균'
  → 평균은 216.67 입니다.

[amount diff]        '10 50 30 차이'
  → 차이는 40 입니다.
```

---

## 8. 테스트

Phase 6-2 에서 신규 케이스 **39 개** 추가 (총 186/186 green):

| 파일 | 케이스 |
|---|---|
| `test_workflow_field_spec.py` | 6 (type 화이트리스트 / enum 일관성) |
| `test_workflow_input_extractor.py` | 16 (regex 경로 12 + LLM fallback 4) |
| `test_workflow_amount_calculation.py` | 10 (sum/avg/diff/invalid/missing/auto-register) |
| `test_workflow_date_calculation.py` (추가분) | 2 (schema 노출 / 자연어 e2e) |
| `test_workflow_reply.py` (추가분) | 3 (amount op 별 포맷) |
| `test_workflow_node.py` (추가분) | 2 (자연어 e2e / 토큰 usage 기록) |

---

## 9. 확인 포인트

### 회귀
- [x] `workflow_key=''` 인 기존 RouterRule → single_shot 폴백 유지.
- [x] 기존 Phase 6-1 테스트 (145) + Phase 5 (114) 전건 통과.
- [x] LLM 실패가 사용자에게 API 오류로 보이지 않음 — MISSING_INPUT 가이드 메시지로 surface.

### 한계 (Phase 6-3 이후)
- 상대 날짜(`오늘`, `어제`, `이번 달`) — LLM 프롬프트가 "절대 표기만" 이라 거부. time-helper 도입 별도 Phase.
- 한글 수사(`천만`, `3억`) — parse_int_like 범위 밖.
- 멀티-턴 "어느 두 날짜요?" 되묻기 — 세션 상태 필요.
- `table_lookup`, `document_compare` 등 나머지 generic workflow.

### 비용
- 질문이 regex 로 완전히 채워지는 경우 LLM 호출 0회.
- regex 가 놓친 required 필드가 있을 때만 cheap model 한 번 호출.
- 로그로 fallback 빈도 관측 가능 (`INFO workflow_input_extractor LLM 보강:`).

---

## 10. 완료 정의 (Definition of Done) 충족 여부

- [x] `FieldSpec` + `WorkflowEntry.input_schema` 도입.
- [x] `workflow_input_extractor` regex + LLM fallback 하이브리드 구현.
- [x] `workflow_node` 가 extractor 를 호출해 자연어 질문에서 자동으로 input 을 채운다.
- [x] `amount_calculation` 이 registry 에 자동 등록되고 sum / average / diff 계산.
- [x] BO `/bo/router-rules/new/` 드롭다운에 `date_calculation` + `amount_calculation` 노출.
- [x] 채팅 end-to-end: 며칠? → 31일 / 평균? → 216.67.
- [x] `chat.tests` 전건 통과 (186/186).
- [x] README §3-1 / §11 + Phase 6-2 dev log 반영.
- [x] 기존 workflow_key 가 비어있는 RouterRule 동작 회귀 0.

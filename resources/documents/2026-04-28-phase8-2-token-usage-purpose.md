# 2026-04-28 개발 로그 — 2.0.0 Phase 8-2: TokenUsage Purpose / Agent Observability 기초

## 배경

Phase 8-1 머지 직후 시점:

- `chat.models.TokenUsage` 는 `model / prompt_tokens / completion_tokens / total_tokens / created_at` 5 필드만 보유 — **호출 목적 구분 불가**.
- `record_token_usage(model, usage)` 가 모든 호출자의 단일 진입점이지만 purpose 의 흔적이 코드에도 DB 에도 없음.
- 운영 관점에서는 7 종류의 LLM 호출 (single_shot 답변 / 3 경로의 query rewriter / workflow extractor / table_lookup / agent ReAct step / agent final_answer) 이 모두 같은 행 형태로 섞여 쌓임.

이 상태에선 "agent reasoning 비용 vs rewriter 비용 vs table_lookup 비용" 같은 비용 분석이 불가능. Phase 8-2 가 이 빈칸을 채운다.

---

## 1. 패키지 구조 변화

```
chat/
  models.py                       ← TokenUsage.purpose CharField + db_index 추가
  migrations/
    0011_tokenusage_purpose.py    ← AddField + default='unknown' (자동 생성)
  services/
    token_purpose.py              ← 신규: 7 상수 + ALL_PURPOSES + validate_purpose
    single_shot/
      postprocess.py              ← record_token_usage 시그니처 keyword-only widening
      pipeline.py                 ← 2 호출 사이트 (rewriter / answer) purpose 명시
    agent/
      react.py                    ← record 위치 _parse_action 직후로 이동 + step/final 분기
  graph/nodes/
    workflow.py                   ← 2 호출 사이트 (rewriter / extractor) purpose 명시
    agent.py                      ← rewriter 호출 purpose 명시
  workflows/domains/general/
    table_lookup.py               ← cell selection LLM purpose 명시
  tests/
    test_token_purpose.py                      ← 신규 (6 cases)
    test_postprocess_record_token_usage.py     ← 신규 (3 cases)
    test_token_usage_purpose_call_sites.py     ← 신규 (9 cases — Step 3+4 통합)
    test_agent_node.py                         ← 기존 mock 어서션 갱신
```

---

## 2. 핵심 결정

### Decision 1 — `purpose` 의 타입: CharField + 코드 상수 (DB enum 아님)

DB 측은 자유 문자열로 두고 (`max_length=32, default='unknown'`) 선택지는 `chat/services/token_purpose.py` 의 상수가 single source of truth. `validate_purpose` 가 record 진입 시 알 수 없는 값을 'unknown' 으로 절감 + warning 로그 — **호출부 오타가 데이터 오염으로 직결되지 않음**.

이유:
- DB 마이그레이션 단순 (default + db_index 만 추가).
- 외부 import / 테스트 fixture 가 모르는 purpose 를 가져와도 전체 시스템이 깨지지 않음.
- 후속 Phase 가 새 purpose 추가 시 코드 한 줄 + ALL_PURPOSES 갱신만.

### Decision 2 — Migration: `AddField + default`, 별도 backfill 없음

`AddField(default='unknown', db_index=True)` 한 번의 ALTER TABLE 로 끝. 기존 row 가 자동으로 'unknown' 분류 → BO 분해 집계 (후속 Phase) 에서 "8-2 이전" 데이터는 'unknown' bucket 에 모이는 게 정확한 의미.

### Decision 3 — `record_token_usage(model, usage, *, purpose=PURPOSE_UNKNOWN)`

keyword-only widening — **positional 시그니처 보존**. 결과:

- 기존 `record_token_usage(rewriter_model, rewriter_usage)` 호출은 default 'unknown' 으로 무회귀.
- 7 사이트가 본 PR 안에서 명시 전달로 전환 → 머지 후 모든 신규 row 는 정확한 purpose.
- `validate_purpose` 방어망은 진입 직후 1회 — 호출부 오타가 여기서 절감됨.

### Decision 4 — agent ReAct 의 `agent_step` vs `agent_final` call-site 분기

`record_token_usage` 위치를 `_parse_action(raw)` 호출 **직후** 로 이동. `action == 'final_answer'` 이면 `PURPOSE_AGENT_FINAL`, 아니면 `PURPOSE_AGENT_STEP` (parse 실패로 action=None 이어도 step 으로 분류 — token 자체는 발생).

이유: 운영 관점에서 "agent 가 답 만들 때 든 비용" vs "탐색 step 에 든 비용" 분리. 후자가 비대해지면 max_iterations / 도구 결정 정책 검토 신호.

### Decision 5 — `query_rewriter` 는 3 호출 경로 통합 purpose

single_shot / workflow / agent 세 경로 모두 같은 `rewrite_query_with_history` 함수를 호출. 비용 패턴이 호출 경로와 무관하므로 purpose 단일 (`'query_rewriter'`). 경로별 분해가 필요해지면 후속 Phase 에서 `purpose` 가 아닌 별 차원 (예: `route` 메타) 으로 추가.

특히 workflow 경로는 `_schema_needs_retrieval` 휴리스틱으로 추가 게이트 — `input_schema` 에 `'text'` 타입 필드가 있는 workflow (table_lookup) 만 rewriter 호출. date_calculation / amount_calculation 은 history 가 있어도 호출 0.

---

## 3. 사용자-가시 변화

**없음** — 8-2 는 운영자 표면. 사용자 답변 본문 / 출처 / UI 변화 없음.

---

## 4. 운영자-가시 변화

| 시나리오 | Before | After |
|---|---|---|
| 채팅 한 번 보내고 `TokenUsage` 행 확인 | `purpose` 컬럼 없음 — 호출 목적 미상 | `purpose='query_rewriter' / 'single_shot_answer' / ...` 정확한 분류 |
| BO 대시보드 일별 합계 | 변화 없음 (8-3 에서 분해 컬럼 추가 예정) | 변화 없음 |
| `unknown` purpose | 존재 안 함 | 8-2 머지 이전 row 가 일괄 'unknown' / 신규 row 는 호출자가 명시 → 0 |

---

## 5. 검증

### 단위 테스트

| 모듈 | 신규 케이스 |
|---|---|
| `test_token_purpose.py` | 6 (멤버십 / validate 정상·오타·빈문자열) |
| `test_postprocess_record_token_usage.py` | 3 (default / 명시 전달 / 오타 절감) |
| `test_token_usage_purpose_call_sites.py` | 9 (single_shot 2 / workflow 2 / agent_node 1 / table_lookup 1 / agent.react 3) |
| **총합** | **18** |

총 424/424 그린 (Phase 8-1 종료 시점 406 → +18). 추가 1건은 기존 `test_agent_node` 의 mock 어서션 갱신 (purpose= 추가).

### 사용자-facing smoke (운영자 검증)

`base_max_id` 기록 후 5 시나리오 시퀀스:

1. 첫 질문 (history 없음, single_shot) → `single_shot_answer`
2. 후속 질문 (single_shot) → `query_rewriter` + `single_shot_answer`
3. 날짜 계산 (workflow `date_calculation`, schema text 없음) → `workflow_extractor`
4. 표 조회 (workflow `table_lookup`, schema text 있음) → `query_rewriter` + `workflow_extractor` + `workflow_table_lookup`
5. 비교형 (agent) → `query_rewriter` + `agent_step` × N + `agent_final` × 1

**통과 기준**:
- known 6종 모두 신규 row 에 등장.
- 신규 row `unknown` 카운트 0.
- `agent_final` 정확히 1, `agent_step` ≥ 1.

### 운영 환경 smoke 한계 (2026-04-28)

본 PR 머지 전 smoke 시점 운영 환경에 **표 데이터를 가진 PDF 가 업로드되어 있지 않아** `workflow_table_lookup` purpose 는 운영 환경 smoke 로 발동하지 못함. 시나리오 4 의 `table_lookup` workflow 가 router 매칭은 됐어도 `retrieve_documents` 가 표를 포함한 청크를 못 찾아 `WorkflowResult.not_found` early return 직전까지 도달해서 LLM 호출 단계에 못 미침 (혹은 router 자체에서 single_shot 으로 떨어짐 — 분포상 `workflow_extractor=1` 단일이라 후자가 더 정합).

대신 unit test (`chat/tests/test_token_usage_purpose_call_sites.py`의 `TableLookupPurposeTests`) 가 `_ask_llm_for_cell` 호출 시 `record_token_usage(..., purpose=PURPOSE_WORKFLOW_TABLE_LOOKUP)` 가 정확히 발생함을 mock 으로 회귀 가드 중. 즉 **purpose 전달 메커니즘 자체는 검증됨** — 운영 환경에서 발동만 못 한 것. 표 PDF 가 추가되면 후속 smoke 로 자연스레 확인됨.

운영 smoke 분포 결과 (base_max_id=434, 5 시나리오 × 2회 누적 25 row):

| purpose | 카운트 | 비고 |
|---|---|---|
| `agent_final` | 2 | 시나리오 5 ×2회 |
| `agent_step` | 8 | 시나리오 5 의 ReAct iterations |
| `query_rewriter` | 10 | 3 호출 경로 (single_shot / workflow / agent) 누적 |
| `workflow_extractor` | 1 | 시나리오 3 (date_calculation) 회귀 가드 |
| `single_shot_answer` | 4 | 시나리오 2 / 4 (4가 single_shot 으로 떨어짐 추정) |
| `workflow_table_lookup` | 0 | unit test 만 회귀 가드 (운영 한계) |
| `unknown` | 0 | 누락 호출 0 ✓ |

---

## 6. 후속 (Phase 8-3 / 후속)

- BO 대시보드의 purpose 분해 컬럼 / 차트 / 모델 × purpose cross-tab — 8-3 책임.
- TokenUsage `cost_usd` 필드 (모델 단가 매핑).
- 외부 SaaS observability (LangSmith / Datadog / OpenTelemetry).
- agent step timeline UI / 비용 예산 알림.
- 8-1 polish backlog (retrieve false positive) — 8-4 polish 에서 일괄 처리.

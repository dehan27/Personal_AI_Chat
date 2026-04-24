# 2026-04-24 개발 로그 — 2.0.0 Phase 4-2: BO Router Rule 관리

## 배경
Phase 4-1 에서 `question_router` 가 코드 상수 기반으로 3분류(`single_shot / workflow / agent`)를 살렸지만, 키워드가 파이썬 튜플에 박혀 있어 새 질문 패턴이 등장할 때마다 재배포가 필요했다.

Phase 4-2 는 이 위에 **운영자가 BO 에서 rule 을 CRUD 하는 DB 오버라이드 레이어**를 얹는다. 코드 상수는 그대로 두고 "기본 동작" 역할을 맡기며, DB rule 은 운영 중 조정 가능한 override 로 동작한다. DB 가 비어있으면 Phase 4-1 상태 그대로 작동하므로 롤백이 쉽다.

---

## 1. 패키지 구조

```
chat/
  models.py                      # RouterRule 모델 추가
  admin.py                       # admin.site.register(RouterRule) (비상 백업)
  migrations/
    0008_routerrule.py           # 스키마 생성
    0009_alter_routerrule_*.py   # help_text 문구 정리 (운영자 시점)
  services/
    question_router.py           # _match_db_rules → keyword fallback → default
bo/
  views/
    router_rules.py              # ModelForm + 5 view 함수
  templates/bo/
    router_rules.html            # 목록 + 기본 키워드 접이식 표시
    router_rule_form.html        # 생성/수정 공용 폼
  urls.py                        # 5 path
  static/bo/bo.css               # FORM 섹션 포트 + 테이블 정렬 변경
```

---

## 2. 데이터 모델

`chat.models.RouterRule` — 설계 §5-4 기반.

| 필드 | 타입 | 비고 |
|---|---|---|
| name | CharField(100) | 운영자 식별용 |
| route | TextChoices | single_shot / workflow / agent — 리터럴은 `chat.graph.routes` 상수와 동일 |
| match_type | TextChoices | 현재 `contains` 만 (regex/exact/negative 는 Phase 5+ 확장 후보) |
| pattern | CharField(256) | 실제 매칭 대상 문자열 |
| priority | Integer (default 100) | 클수록 먼저 평가 |
| enabled | Boolean (default True) | 삭제 없이 무력화 가능 |
| description | TextField(blank) | 변경 이유 메모 |
| created_at / updated_at | auto | |

`Meta.ordering = ['-priority', '-updated_at']` — router 가 별도 정렬 없이 순회 가능.

`unique_together` 없음. 같은 pattern 을 서로 다른 route/priority 로 중복 등록하는 시나리오(예: 점진적 실험)를 허용.

---

## 3. Router 우선순위 변경

`chat/services/question_router.py` 에 `_match_db_rules` 추가.

```python
def route_question(question: str) -> RouteDecision:
    db_decision = _match_db_rules(question)
    if db_decision is not None:
        return db_decision
    # (Phase 4-1 keyword fallback + default single_shot)
```

포인트:
- **lazy import** — `from chat.models import RouterRule` 를 함수 안에서 호출해 Django app 로딩 순서와 무관.
- **reason 형식** — `db_rule:<name>` / `workflow_keyword` / `agent_keyword` / `default`. 운영 로그에서 어느 rule 이 터졌는지 즉시 식별.
- **매 요청 DB 조회** — 설계 §5-9 옵션 1. 초기 rule 수 <100 수준이면 부하 문제 없음. TTL 캐시는 Phase 5 이후 판단.

---

## 4. BO UI

`/bo/router-rules/` 5 path:
- `/` — 목록 (+ 코드 기본 키워드 접이식 카드)
- `/new/` — 생성
- `/<pk>/edit/` — 수정
- `/<pk>/toggle/` — enabled POST 반전
- `/<pk>/delete/` — POST 삭제

`ModelForm` 으로 폼 렌더. 위젯 attrs 에 `class="input"` 주입해 디자인 가이드라인 Form 규격 그대로 적용.

사이드바에 `라우팅 관리` 항목 (Prompt 관리 다음), git-branch 아이콘.

### 디자인 가이드라인 준수 리팩터

초기 구현은 커스텀 클래스(`.route-badge-*`, `.rule-table`, `.rules-empty-hint`, `.defaults-card`)를 섞어 썼다. 가이드라인 대조 후 다음으로 교체:

- 배지 — `.badge .badge-info` (workflow), `.badge .badge-warning` (agent), `.badge` (single_shot)
- 테이블 — `.table-wrap` > `<table class="table">`
- 빈 상태 — `.empty-state` / `.empty-state-title`
- 기본 키워드 카드 — `.card` 컴포넌트

### bo.css FORM 섹션 포트

`router_rule_form.html` 이 `.form-group / .form-label / .input / .form-hint / .form-error` 를 가이드라인 그대로 쓰는데 `bo.css` 에는 해당 섹션이 없었다. 가이드라인에서 해당 규칙을 포트, 추가로:
- `textarea.input { resize: vertical; max-width: 100%; min-height: calc(var(--line-height-normal) * 3em); }` — description 이 뷰포트 밖으로 확장되던 문제 해결.
- `@keyframes shake` + `.input.shake` — 필수값 누락 시 0.45s 흔들림.

### 필수 필드 UX

`<form novalidate>` + JS 커스텀 검증:
- 레이블 뒤 `*` 마커(`color: var(--color-danger)`).
- 빈 필드 제출 시 `.is-error` + `.shake` + 인풋 바로 아래 `<p class="form-error" data-client-error>` 표시 + `scrollIntoView` + `focus`.
- `input` / `change` 이벤트로만 에러 해제 — `focus` 포함 시 제출 직후 자동 포커스가 빨간 테두리를 즉시 지워 UX 가 깨졌음(`router_rule_form.html` 스크립트 주석 참조).
- 에러 문구는 `input → error → hint` 순서로 배치해 인풋에 바로 붙음.

### help_text 운영자 관점 리라이트

초기 help_text 는 "매칭할 키워드/패턴 (match_type 에 따라 해석)" 같은 개발자 표현이었다. 운영자 기준으로 재작성:

- pattern → 실예시(`"퇴직금"` → `"퇴직금 얼마야?"`) + 금지 기호(`/`, `#`, 따옴표) + 띄어쓰기 비교 주의 + 변형 많으면 분리 권장
- match_type → 4개 방식 불릿 설명 (포함 / 정확히 일치 / 정규식 / 제외)
- route → 3개 처리 방식 불릿 (single-shot / workflow / agent)
- priority → 권장 범위 (일반 100, 우선 200+)
- enabled → "삭제 없이 끄는 용도" 명시

멀티라인은 모델에 `\n` 으로 저장하고 템플릿에서 `|linebreaksbr` 로 `<br>` 렌더.

### 용어 정리

- `rule` → `규칙` (UI 전체)
- `패턴` → `키워드` (폼 라벨·목록 헤더·help_text)
- `경로 (route)` → `처리 방식` (폼 라벨·목록 헤더·페이지 설명)

DB 필드명(`pattern`, `route`) 은 마이그레이션 최소화를 위해 그대로 유지. 라우터 코드도 불변.

### 테이블 중앙 정렬

`bo.css` 의 `.table th, .table td` 기본 정렬을 `left` → `center` 로 변경. 대시보드·files·router_rules 템플릿의 `text-right` 클래스를 제거해 BO 전 페이지 테이블이 일관되게 중앙 정렬되도록 정리. `.table .text-right` 유틸리티는 그대로 남겨 필요 시 개별 셀 오버라이드 가능.

---

## 5. Admin 등록

```python
# chat/admin.py
admin.site.register(RouterRule)
```

BO 가 장애로 접근 불가할 때 `/admin/` 에서 직접 수정 가능한 비상 백업 경로. 2줄이지만 운영 안전망.

---

## 6. 빈 DB 정책

`0008_routerrule.py` 는 스키마만 생성, 데이터 시드 없음. Phase 4-1 의 `WORKFLOW_KEYWORDS` / `AGENT_KEYWORDS` 상수가 그대로 코드에 남아 fallback 으로 작동하므로 BO 가 비어도 Phase 4-1 동작 유지.

`router_rules.html` 목록 하단에 `<details>` 접이식 카드로 코드 기본 키워드를 읽기 전용 공개 — "BO 가 비어도 기본 동작은 무엇인가" 가 운영자 시선에서 투명해짐.

---

## 7. 검증

### 자동
- `makemigrations --dry-run` 통과, `0009` 까지 적용.
- `docker compose exec -T web python manage.py check` 통과.

### 수동 (브라우저)
- `/bo/router-rules/` 초기 빈 상태 문구 + 기본 키워드 카드 2개 노출
- `+ 새 규칙` → 필수 필드 비운 채 저장 → shake + 에러 메시지 + 첫 invalid 필드 focus
- 저장 후 목록에 priority 내림차순 정렬 확인
- `며칠` 규칙(workflow, priority 200) 등록 후 채팅에서 "올해 내 연차 며칠이야?" 전송 → 서버 로그에 `db_rule:며칠` reason
- 해당 규칙 `enabled` 토글 OFF → 다시 질문 → reason 이 `workflow_keyword` 로 복귀 (코드 fallback)
- 삭제 confirm → 사라짐
- `/admin/router-rules/` 에서도 조회·편집 가능

### 회귀 민감 포인트
- DB 가 비어있을 때 응답이 Phase 4-1 과 동일 (회귀 0)
- 비활성 규칙은 `_match_db_rules` 의 `filter(enabled=True)` 에서 제외
- 같은 pattern 이 여러 규칙에 있어도 `Meta.ordering` 이 `(-priority, -updated_at)` 로 결정적 순서 보장

---

## 8. 남은 작업 / Out of Scope

Phase 4-2 범위 밖 (설계 §5-6 out-of-scope):
- `regex` / `exact` / `negative` match_type 실제 로직
- rule 매칭 미리보기 UI
- rule 간 충돌 감지
- 변경 이력 (created_by / updated_by)
- TTL 캐시
- 승급(escalation) / LLM 보조 분류
- workflow / agent 노드 실체 (Phase 5~7)

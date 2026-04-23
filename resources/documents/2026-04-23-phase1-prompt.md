# 2026-04-23 개발 로그 — 2.0.0 Phase 1: Prompt 관리 분리

## 배경
2.0.0 로드맵의 첫 단계. 지금까지 시스템 프롬프트와 `prompt_builder` 내부 지시문은 Python 상수로 박혀 있어서 한 문장만 바꿔도 재배포가 필요했다. 이후 Phase(LangGraph 진입, router/workflow/agent 프롬프트 추가)에서도 같은 패턴을 재사용할 수 있도록 **파일 기반 프롬프트 관리 + 운영 UI 기초**를 먼저 깐다.

핵심 원칙:
- 기존 `single-shot` 동작은 한 줄도 바뀌지 않는다 (프롬프트 출처만 파일로 이동).
- BO 는 `파일 탐색기`가 아니라 `허용된 Prompt 편집기` — registry 기반 allow-list.
- 로더 인터페이스는 단순하게 유지해 추후 DB 기반으로 바꿔도 호출부가 흔들리지 않게.

---

## 1. 디렉토리 구조

```
assets/
  prompts/
    chat/
      system.md                 ← 기존 SYSTEM_PROMPT
      source_instruction.md     ← 기존 _SOURCES_INSTRUCTION
      qa_instruction.md         ← 기존 _QA_INSTRUCTION
      no_sources_guard.md       ← 기존 _NO_SOURCES_GUARD
```

오타 디렉토리 `assets/promts/` 는 이번에 제거. 향후 phase 에서 `router/`, `workflows/`, `agents/` 등 하위 폴더가 추가될 예정이지만 Phase 1 은 `chat/` 만 실사용.

---

## 2. 설정

`settings.py` 에 추가:
```python
PROMPTS_DIR = Path(os.environ.get('PROMPTS_DIR', BASE_DIR / 'assets' / 'prompts'))
```

- 기본값: repo 내부 `assets/prompts/`
- override: `PROMPTS_DIR` env 로 외부 마운트 경로 지정 가능 (운영에서 재배포 없이 프롬프트 교체)

`env.example.txt` / `env.prod.example.txt` 에 주석과 override 예시 추가.

---

## 3. Prompt Loader (`chat/services/prompt_loader.py`)

**API**
- `load_prompt(relative_path: str) -> str`
- `save_prompt(relative_path: str, content: str) -> None`
- `invalidate_cache(relative_path: str | None = None)` — 테스트/긴급용

**구현 결정**
- **캐시**: 모듈 레벨 `dict` + `RLock` 으로 스레드 안전. 매 요청 디스크 읽기 회피.
- **예외**: 파일 누락 → `PromptNotFound` (배포 누락을 조용히 삼키지 않음). 읽기 실패 등은 일반 `OSError`.
- **보안**: `_resolve_path` 에서 `resolve()` 후 `relative_to(base)` 로 PROMPTS_DIR 밖 경로 차단 (`..` 포함·심볼릭 링크 회피).
- **쓰기 원자성**: `.tmp` 에 먼저 쓰고 `replace()` — 쓰는 중 다른 프로세스가 읽어도 깨지지 않음.
- **줄바꿈 처리**: `read_text(...).rstrip('\n')` — 기존 `.strip()` 후 상수 저장하던 동작과 일치하게.

---

## 4. Prompt Registry (`chat/services/prompt_registry.py`)

`PromptEntry(key, title, description, relative_path, editable)` 의 불변 데이터클래스 리스트. BO 가 이 registry 를 통해서만 파일에 접근.

**초기 항목 4개** (모두 `editable=True`):
- `chat-system`
- `chat-source-instruction`
- `chat-qa-instruction`
- `chat-no-sources-guard`

향후 phase 에서 router/workflow/agent 프롬프트를 등록할 때도 같은 리스트에 항목만 추가.

---

## 5. 기존 서비스 치환

- `chat/services/prompt_builder.py`
  - `from chat.prompt.chat import SYSTEM_PROMPT` 제거
  - 3개 내부 상수 제거
  - 각 호출부에서 `load_prompt('chat/<name>.md')` 로 교체
- `chat/services/history_service.py`
  - `initial_history()` 가 `load_prompt('chat/system.md')` 호출
- `chat/prompt/` 디렉토리 완전 삭제 (남은 참조 0건, `manage.py check` 통과)

---

## 6. BO Prompt 관리 UI

**URL 3개** (`bo/urls.py`)
- `GET /bo/prompts/` — 목록
- `GET /bo/prompts/<key>/` — 편집 페이지
- `POST /bo/prompts/<key>/update/` — 저장

**View 규칙** (`bo/views/prompts.py`)
- unknown key → 에러 메시지와 함께 목록으로 redirect (404 유사 UX)
- `editable=False` → 403
- 빈 content 저장 시도 → 에러 메시지 + 편집 페이지로 복귀
- CRLF/CR 을 LF 로 정규화 후 저장
- 저장 경로는 서버 코드(`entry.relative_path`)에서만 결정. 사용자 입력 path 는 받지 않음

**템플릿**
- `bo/templates/bo/prompts.html` — 카드 리스트. title / description / file path / [편집] 링크
- `bo/templates/bo/prompts_edit.html` — 메타 정보 + 경고 배너 + 420px min-height monospace textarea + 저장/취소 버튼

**UI 기준**
- 기존 BO CSS 토큰(`page-header`, `card`, `alert`, `btn`) 재사용 → `assets/guides/ui/DesignGuideline.html` 체계와 일관
- 사이드바에 `Prompt 관리` 메뉴 추가 (Q&A 관리 다음)

---

## 7. 검증

### 자동
- `docker compose exec web python manage.py check` — 에러 없음
- `python -c "from chat.services.prompt_loader import load_prompt; load_prompt('chat/system.md')"` — 파일에서 원문 정상 로드, 캐시 재호출 시 동일 객체
- `load_prompt('../../../etc/passwd')` → `ValueError` (traversal 차단)
- `load_prompt('chat/does_not_exist.md')` → `PromptNotFound`
- `grep -r "from chat.prompt\|SYSTEM_PROMPT" chat/ bo/` → 0건 (old import 잔재 없음)

### BO 라우트
- `/bo/prompts/` → 200
- `/bo/prompts/chat-system/` → 200, textarea 에 현재 내용 표시
- `/bo/prompts/nonexistent/` → 302 (목록으로 redirect, error 메시지)
- POST `/bo/prompts/chat-system/update/` (정상 content) → 저장 성공, redirect 후 편집 페이지에 반영된 내용 표시

### 엔드투엔드 (브라우저)
- [ ] 채팅 페이지에서 기존 single-shot 질문 동작 확인 (자료 있음 / 자료 없음 두 케이스)
- [ ] BO `Prompt 관리` 진입 → 4개 항목 표시
- [ ] 시스템 프롬프트 끝에 특이 문구 추가 후 저장 → 바로 다음 채팅 응답에 반영
- [ ] 원본으로 되돌려 놓기

---

## 8. 리스크 대응

| 리스크 | 대응 |
|---|---|
| 프롬프트 문구의 미묘한 변화로 회귀 | 기존 Python 상수를 공백·줄바꿈까지 정확히 그대로 `.md` 로 옮김. `rstrip('\n')` 로 파일 말미 개행만 제거해 `.strip()` 원본과 동일 |
| 캐시로 인해 저장이 즉시 반영 안 됨 | `save_prompt` 가 해당 키를 캐시에서 invalidate. 다음 `load_prompt` 에서 디스크 재읽기 |
| 프로덕션 이미지에 `assets/prompts/` 누락 | `.dockerignore` 에 제외 규칙 없음. `Dockerfile.prod` 의 `COPY . .` 이 같이 담음. `docker compose exec web ls /app/assets/prompts/chat/` 로 확인 가능 |
| BO 에서 잘못 저장 | Phase 1 은 rollback UI 미제공. 편집 화면 상단에 "되돌리기 없음" 경고 배너 + 빈 저장 차단 |

---

## 9. Phase 2 로 넘길 것

- LangGraph 기반 single-shot node 래퍼
- router/workflow/agent 프롬프트 실사용 (디렉토리 구조만 준비된 상태)
- DB 기반 프롬프트 저장·버전·diff·rollback
- draft/publish 승인 플로우
- 환경별 프롬프트 분기

---

## 변경 파일 요약

### 신규 (9)
- `assets/prompts/chat/{system,source_instruction,qa_instruction,no_sources_guard}.md`
- `chat/services/prompt_loader.py`
- `chat/services/prompt_registry.py`
- `bo/views/prompts.py`
- `bo/templates/bo/prompts.html`
- `bo/templates/bo/prompts_edit.html`

### 수정 (7)
- `AI_Chat/settings.py`
- `chat/services/prompt_builder.py`
- `chat/services/history_service.py`
- `bo/urls.py`
- `bo/views/__init__.py`
- `bo/templates/bo/base.html`
- `env.example.txt` / `env.prod.example.txt`
- `README.md`

### 삭제 (1 package)
- `chat/prompt/` 전체 (chat.py + __init__.py)

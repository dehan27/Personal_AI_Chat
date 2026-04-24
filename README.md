# AI_Chat — 개인 자료 기반 RAG 챗봇

Django · PostgreSQL(pgvector) · OpenAI를 결합한 개인 문서 Q&A 챗봇입니다.  
업로드한 개인 자료(규정·매뉴얼·노트 등 PDF/DOCX/TXT)를 임베딩해두고, 질문이 들어오면 관련 조각을 찾아 근거 있는 답변을 생성합니다. 백오피스에서 자료를 관리하고, 생성된 Q&A를 검수·승격하여 공식 답변으로 캐싱할 수 있습니다.

---

## 목차

1. [프로젝트 개요](#1-프로젝트-개요)
2. [기술 스택](#2-기술-스택)
3. [앱 구성](#3-앱-구성)
   - [3-1. chat](#3-1-chat)
   - [3-2. files](#3-2-files)
   - [3-3. bo](#3-3-bo-backoffice)
   - [3-4. AI_Chat (프로젝트 루트)](#3-4-ai_chat-프로젝트-루트)
4. [파일 업로드·관리 기술](#4-파일-업로드관리-기술)
5. [업로드 파일 임베딩 방식](#5-업로드-파일-임베딩-방식)
6. [검색 방식](#6-검색-방식)
7. [쿼리(질의응답) 파이프라인](#7-쿼리질의응답-파이프라인)
8. [사용자 피드백 & 관리자 검수 루프](#8-사용자-피드백--관리자-검수-루프)
9. [전체 동작 로직](#9-전체-동작-로직)
10. [배포·실행](#10-배포실행)
    - [10-1. resources/ 디렉토리 구성](#10-1-resources-디렉토리-구성)
    - [10-2. 데이터베이스 접속 정보](#10-2-데이터베이스-접속-정보)
    - [10-3. 확장 포인트](#10-3-확장-포인트)
11. [개발 로그](#11-개발-로그)

---

## 1. 프로젝트 개요

이 시스템은 **사용자가 업로드한 개인 자료만 근거로 답하는 RAG 챗봇**입니다. 일반 LLM은 내 규정·매뉴얼·노트를 알지 못하므로, 문서를 작은 조각(청크)으로 나눠 벡터 형태로 저장해둔 뒤 질문마다 관련 조각만 찾아 프롬프트에 주입합니다. 이를 통해 환각(hallucination)을 줄이고, 자료에 근거한 일관된 답변을 낼 수 있습니다.

핵심 특성

- **근거 있는 답변**: 답변 하단에 참조한 원본 문서 출처 뱃지 표시. 클릭하면 PDF 뷰어가 모달로 열림.
- **관리자 큐레이션**: 사용자가 엄지로 피드백을 남기고, 관리자는 백오피스에서 답변을 검수·편집·공식 승격.
- **답변 일관성**: 공식 승격된 Q&A는 다음에 유사 질문이 오면 그대로 재사용 (캐시 히트).
- **풀스크린 임베드**: 채팅 UI는 뷰포트 전체를 채우도록 설계되어 다른 웹사이트에 iframe으로 쉽게 삽입 가능.

---

## 2. 기술 스택

**백엔드**
- Django 5.2 (Python 3.11)
- PostgreSQL 17 + `pgvector` 확장(HNSW 인덱스) + `pg_trgm` 확장(GIN 인덱스)
- OpenAI API — 임베딩(text-embedding-3-small, 1536차원) / 답변(gpt-4o-mini) / 재정렬(gpt-4o-mini)

**문서 처리**
- PyMuPDF (fitz) — PDF 주 추출 엔진. 한글 레이아웃·읽기순서 품질 우수.
- pdfplumber — 표 감지 기반 PDF 대체 엔진. 마크다운 표 변환 지원.
- pypdf — 위 두 엔진 실패 시 최후 폴백.
- python-docx — DOCX 문단·표 추출.
- tiktoken — OpenAI 토크나이저. 청크 크기 계산·분할 기준.

**프런트엔드**
- Django 템플릿 (서버 렌더링) + 순수 JavaScript
- `marked.js` + `DOMPurify` — 봇 답변 마크다운 렌더링(안전하게 sanitize)
- Noto Sans KR 웹폰트
- CSS 디자인 토큰 기반 디자인 시스템 (단일 파일 스타일시트)

**인프라**
- Docker Compose — web(Django) + db(pgvector/pgvector:pg17) 2개 컨테이너
- 호스트 포트: web 8001 / db 5432
- Docker 볼륨으로 DB 영속화

---

## 3. 앱 구성

### 3-1. chat

챗봇의 **질의응답 핵심**을 담당하는 앱.

**모델**
- `ChatLog` — 자료 기반 답변이 나온 모든 채팅 기록. 질문 임베딩·답변·참조 문서 ID를 보관하며 피드백이 연결됨. 저장 시 유사 질문(0.90 이상)이 이미 있으면 새로 생성하지 않고 기존 레코드를 재사용해 피드백이 분산되지 않게 함.
- `CanonicalQA` — 관리자가 승격한 공식 Q&A. RAG 쿼리 시 실제 검색 대상. `source_chatlog` 외래키로 어느 ChatLog에서 승격됐는지 추적.
- `Feedback` — ChatLog에 붙는 👍/👎 레코드. rating과 생성시각만 가짐.
- `TokenUsage` — 모든 OpenAI 호출의 토큰 소비 로그. 대시보드 집계에 사용.

**서비스 레이어**
- `single_shot/` — 질문 하나 → 답변 하나 경로를 단계별 helper 로 분해한 패키지. `pipeline.run_single_shot` 이 외부 진입점이고, 내부에 `retrieval` / `qa_cache` / `prompting` / `llm` / `postprocess` / `types` 가 있음. 공통 규칙(history·error·token·ChatLog·sources·분류)은 `__init__.py` docstring 에 명문화. Phase 4-3 부터는 retrieval 앞단에 `query_rewriter` 를 두어 "비싼거" 같이 맥락에 의존하는 후속 질문을 self-contained 검색어로 바꿔 넘긴다 (원본 질문은 LLM·ChatLog 에 그대로 유지).
- `query_rewriter` — 대화 history + 현재 질문을 cheap LLM 에 보내 자립 검색어를 만드는 helper. history 가 비거나 호출 실패 시 원본 질문을 그대로 반환해 회귀 0 을 보장. 프롬프트는 `assets/prompts/chat/query_rewriter.md` 파일로 분리돼 BO 에서 편집 가능.
- `graph/` — LangGraph 실행 구조. `app.run_chat_graph` → `router` → `(single_shot / workflow / agent)`. Phase 4-1 기준 workflow·agent 는 single_shot 노드로 내부 포워딩. route 상수는 `chat/graph/routes.py`.
- `question_router` — `route_question(question) -> RouteDecision`. Phase 4-2 부터 **DB `RouterRule` 먼저 조회 → 매치 없으면 코드 상수(`WORKFLOW_KEYWORDS` / `AGENT_KEYWORDS`) fallback → 그래도 없으면 `single_shot`**. DB 가 비어있으면 코드 키워드로 Phase 4-1 동작 그대로 유지. BO `/bo/router-rules/` 에서 운영자가 rule 을 CRUD. Phase 6-1 부터 결정에 `workflow_key` 가 같이 실려 내려가지만, 코드 키워드 fallback 은 key 를 **설정하지 않으므로**(항상 `''`) 기존 single_shot 포워딩 동작은 그대로다. 어떤 generic workflow 로 보낼지는 **BO RouterRule 의 `workflow_key` 선택 한 곳**에서만 결정된다.
- `prompt_builder` — 시스템·히스토리·자료·참고 Q&A·질문을 조립해 OpenAI 메시지 배열로 변환 (Phase 1 에서 파일 기반으로 전환됨).
- `prompt_loader` / `prompt_registry` — `assets/prompts/` 하위 파일을 읽는 로더와 BO 편집 허용 목록(Phase 1).
- `qa_retriever` — CanonicalQA 벡터 검색, ChatLog 저장(중복 방지), ChatLog→CanonicalQA 승격.
- `reranker` — 하이브리드 검색 결과 top 10을 GPT에게 재정렬 요청해 실제 관련성 상위 top 5를 고름.
- `history_service` — 세션 기반 대화 히스토리 CRUD (view 에서만 쓰고, 서비스·graph 는 read-only 로만 받음).
- `workflows/core/` — 도메인 무관 공통 레이어 (Phase 5). 날짜 파싱·기간 계산(`dates`), 숫자·금액 정규화(`numbers`), 입력 검증(`validation`), 결과 타입(`result` · `ValidationResult` / `WorkflowResult`), 표시용 포맷(`formatting`), `BaseWorkflow` Protocol + `run_workflow` 러너(`base`) 가 있다. Phase 6 도메인 workflow(퇴직금 / 연차 / 근속 등)가 여기 helper 만 조합해 쓰도록 유도. 의존 방향은 일방향 — `result ← validation / dates / numbers / formatting ← base`.

**뷰**
- `home` — 채팅 메인 페이지(index.html) 렌더.
- `message` — POST로 받은 질문을 파이프라인에 넘기고 JSON 응답.
- `reset` — 세션 히스토리 초기화.
- `feedback` — 👍/👎 피드백 저장.

**정적·템플릿**
- `static/chat/chat.css` · `chat.js` — 메시지 버블, 아바타, 출처 뱃지, PDF 모달, 피드백 버튼, 마크다운 렌더.
- `templates/chat/index.html` — 풀스크린 레이아웃, 상단 헤더, 중앙 메시지 영역, 하단 입력창.

---

### 3-2. files

업로드된 **개인 자료의 수명주기**를 담당하는 앱.

**모델**
- `Document` — 원본 파일 메타데이터. 파일명·크기·MIME·상태(pending/reviewing/processing/ready/failed)·에러 메시지·추출된 텍스트(편집 가능)·업로드 시각을 보관.
- `DocumentChunk` — 문서를 잘라낸 조각. content(텍스트)·embedding(1536차원 벡터)·chunk_index·metadata(JSON)를 가지며 Document에 외래키로 연결. HNSW 벡터 인덱스와 pg_trgm GIN 인덱스가 함께 걸려 있음.

**서비스 레이어**
- `extractor` — 확장자별 텍스트 추출. PyMuPDF → pdfplumber → pypdf 3단 폴백. DOCX는 문단과 표를 마크다운으로 변환.
- `chunker` — tiktoken 기반 토큰 단위 분할. 문단→줄→문장 순으로 경계를 존중하며, 기본 500토큰·오버랩 100토큰.
- `embedder` — OpenAI 임베딩 API 호출. 배치 처리(100개씩)와 3회 재시도.
- `retriever` — 벡터 검색 + 키워드 검색(ILIKE, 매칭 수 기준) + RRF 병합. 상위 N개 ChunkHit 반환.
- `pipeline` — 위를 엮는 두 단계: `extract_document`(파일→텍스트, 상태 REVIEWING)와 `finalize_document`(텍스트→청킹→임베딩→저장, 상태 READY). 사용자 편집 단계를 중간에 끼울 수 있게 분리됨.

---

### 3-3. bo (backoffice)

**관리자 전용 페이지**. 사이드바 내비게이션 + 본문 영역 레이아웃.

**서브 섹션**
- **대시보드** (`/bo/`) — 최근 7일간 토큰 사용량 집계 카드 + 일별 테이블. TokenUsage 기반. 타이틀 우측의 `API 사용량` 버튼을 누르면 OpenAI Admin API 로 집계한 **전체 누적 / 최근 7일 / 모델별 분해 + 비용** 이 모달로 열린다 (Phase 4-4).
- **파일관리** (`/bo/files/`) — 업로드·목록·삭제. 목록은 5개 단위 페이지네이션. 업로드 후 검토 페이지로 자동 이동.
  - **검토 페이지** (`/bo/files/<id>/review/`) — PyMuPDF가 추출한 텍스트를 좌측 정보 패널과 함께 편집 가능한 textarea로 표시. 글자 수·토큰 수·예상 청크 수·예상 임베딩 비용까지 실시간 안내. "임베딩 진행" 버튼을 누르면 finalize_document가 실행되어 청킹·임베딩·저장이 완료됨. "취소 및 삭제"로 되돌릴 수도 있음.
- **Q&A 관리** — 세 개의 서브 탭으로 구성.
  - **대화 로그** (`/bo/qa/logs/`) — 모든 ChatLog를 카드 형태로 나열. 👍/👎 카운트, 승격 여부 뱃지, "공식 승격" 버튼·"삭제" 버튼. 미승격/승격됨/전체 탭 필터.
  - **답변 응답** (`/bo/qa/feedback/`) — 피드백이 달린 ChatLog만. 다수결 기준(👎≥👍 and 👎>0 → 나쁨 / 👍>👎 → 좋음) 탭 필터.
  - **공식 Q&A** (`/bo/qa/canonical/`) — CanonicalQA 목록. 질문·답변 인라인 편집, 삭제. 편집 시 질문 임베딩도 자동 재계산.

---

### 3-4. AI_Chat (프로젝트 루트)

프로젝트 레벨 설정·라우팅. 앱이 아니라 Django project의 중심 디렉토리.

- `settings.py` — `INSTALLED_APPS`에 chat·bo·files 등록. 데이터베이스 연결(PostgreSQL), 미디어 설정(`MEDIA_ROOT=resources/`, `MEDIA_URL=/media/`), `X_FRAME_OPTIONS=SAMEORIGIN`(PDF 모달용), 한국 시간대.
- `urls.py` — 루트 `/`는 chat 앱 포함, `/bo/`는 bo 앱 포함, `/admin/`은 Django 내장 관리자. 개발 모드에서만 미디어 파일 서빙 추가.
- `wsgi.py`·`asgi.py` — 서버 진입점.

---

## 4. 파일 업로드·관리 기술

**저장 위치**
- 파일 실체: 호스트 `resources/origin/` 디렉토리(컨테이너는 `/app/resources/origin/`). Django의 `FileField(upload_to='origin/')` + `MEDIA_ROOT=resources/`.
- 메타데이터: PostgreSQL의 `files_document` 테이블.
- 조각 + 벡터: PostgreSQL의 `files_documentchunk` 테이블.

**무결성 보장**
- `DocumentChunk → Document` FK에 `on_delete=CASCADE` — 원본 삭제 시 청크 자동 삭제.
- 파일 삭제 뷰(`bo.views.delete`)는 `doc.file.delete()`로 디스크도 동시 정리 + 이 문서를 sources에 참조하는 `ChatLog`·`CanonicalQA`까지 함께 삭제해 고아 데이터를 만들지 않음.
- 업로드 검증: 확장자 화이트리스트(`.txt`/`.md`/`.pdf`/`.docx`), 최대 20MB.
- 중복 파일명: Django `FileField`가 자동 해시 접미사를 붙여 충돌 방지.

**상태 기계**
```
pending → processing → reviewing → processing → ready
                                ↘ failed (재시도 가능)
```
검토 단계를 넣어 **추출된 텍스트를 관리자가 확인·수정한 뒤에만** 임베딩이 진행되도록 분기.

**업로드 허용 외의 운영 원칙**
- 디스크 파일을 직접 옮기지 말 것 — DB와 불일치 발생. 반드시 BO의 삭제 버튼 사용.
- 대량 정리는 `Document.objects.all().delete()` + 디스크 비우기로 깔끔히.

---

## 5. 업로드 파일 임베딩 방식

**단계별 흐름**

1. **업로드 및 저장** — 파일 업로드 즉시 `Document`가 `status=PENDING`으로 생성되고 디스크에 저장됨.
2. **텍스트 추출** — PyMuPDF가 1순위. 읽기 순서(reading order) 기반이라 한글 규정 문서처럼 복잡한 레이아웃도 대체로 안전하게 평문화함. 실패 시 pdfplumber(표 탐지 지원)로 폴백. 그래도 실패하면 pypdf 최후 폴백. DOCX는 python-docx로 문단과 표를 마크다운 형식으로 변환. TXT/MD는 UTF-8 우선·CP949(한글 윈도우) 폴백으로 읽음.
3. **검토 대기** — 추출 결과가 `Document.edited_text`에 저장되고 `status=REVIEWING`으로 전환. 관리자는 검토 페이지에서 오탈자·파편화를 직접 수정할 수 있음.
4. **청킹** — 관리자가 "임베딩 진행"을 누르면 편집된 텍스트를 tiktoken으로 500토큰 단위 분할. 문단(`\n\n`)→줄→문장 순으로 경계를 살려 의미 단위 분할. 청크 사이에 100토큰 오버랩을 두어 경계에서 맥락이 끊기지 않게 함.
5. **임베딩** — 청크 리스트를 OpenAI 배치 임베딩 API(text-embedding-3-small)에 보냄. 한 호출당 최대 100개씩, 네트워크 실패 시 3회 재시도. 결과는 1536차원 실수 벡터 배열.
6. **저장** — 트랜잭션 안에서 기존 청크를 삭제하고 `DocumentChunk.objects.bulk_create`로 일괄 삽입. 각 청크는 content·embedding·chunk_index·metadata를 가짐. 트랜잭션으로 원자성 보장 — 중간 실패 시 전부 롤백.
7. **인덱스 활용** — DB에 미리 만들어둔 HNSW(코사인) 인덱스와 pg_trgm(trigram) GIN 인덱스가 자동으로 새 청크를 색인.

**왜 이렇게 쪼개나**
- 문서 전체를 하나의 벡터로 만들면 어떤 질문에도 중간 값이라 정밀도가 떨어짐.
- 청크가 작을수록 "정확히 경조사 휴가 조항"만 걸리는 식의 정밀 매칭이 가능.
- 오버랩은 청크가 기계적으로 잘려 의미가 반쪽이 되는 것을 방지.

---

## 6. 검색 방식

이 프로젝트의 검색은 **다층 구조**입니다.

**① 벡터 검색 — 의미 유사도**
- 질문을 같은 임베딩 모델로 1536차원 벡터로 변환.
- pgvector의 `<=>` 연산자(코사인 거리)와 HNSW 인덱스를 이용해 상위 N개를 수십 ms 이내로 추출.
- 단어 철자가 달라도 의미가 비슷하면 걸림 ("경조사 휴가" ↔ "애도 휴가").

**② 키워드 검색 — 명시 단어 매칭**
- 질문에서 의미 있는 단어(2글자 이상, 불용어 제거)를 추출.
- 각 키워드를 `content ILIKE '%키워드%'`로 검색하며, 매칭된 키워드 수로 내림차순 정렬.
- pg_trgm GIN 인덱스가 ILIKE 가속.
- "대표자" "전화번호" 같은 메타 정보나 표 안의 고유명사를 정확히 잡아냄 — 벡터 검색만으론 놓치는 경우 구제.

**③ RRF 병합 — Reciprocal Rank Fusion**
- 벡터·키워드 각각 상위 20개의 순위를 매긴 뒤 `1/(60+rank)` 공식으로 점수 합산.
- 두 검색 모두에서 상위에 있는 청크가 가장 높은 합산 점수를 얻음.
- 한 쪽에서만 잘되는 청크도 점수 일부를 얻어 구제될 수 있음.

**④ LLM 재정렬 — 문맥 이해 기반 재랭킹**
- RRF 상위 10개를 gpt-4o-mini에게 "이 질문과 이 청크들의 실제 관련성 순서"로 다시 정렬하라고 요청(temperature=0, JSON 응답).
- 표면적 유사도와 실제 의도의 관련성 차이를 GPT가 판별.
- 결과 상위 5개만 최종 프롬프트에 들어감.

**⑤ CanonicalQA 검색 — 공식 Q&A 재사용**
- 관리자가 승격한 공식 Q&A를 벡터 검색(엄격한 임계값 0.80)으로 찾음.
- 유사도 0.88 이상이면 캐시 히트로 판단해 **OpenAI 생성 호출 없이** 공식 답변을 그대로 반환. 일관성·속도·비용 동시 해결.
- 0.80~0.88 범위면 캐시는 아니지만 "과거 참고 답변" 섹션으로 프롬프트에 포함되어 GPT가 톤·형식을 참고.

---

## 7. 쿼리(질의응답) 파이프라인

사용자가 질문을 보내면 다음 순서로 처리됩니다.

1. **세션 히스토리 로드** — Django 세션에서 최근 대화 턴(최대 20개)을 가져와 맥락 유지.
2. **자료 후보 검색** — 하이브리드 검색(벡터 + 키워드 + RRF)으로 DocumentChunk 상위 10개 후보 선정.
3. **LLM 재정렬** — 후보 10개를 gpt-4o-mini가 관련성 기준으로 다시 순위. 상위 5개만 최종 채택.
4. **공식 Q&A 검색** — CanonicalQA에서 유사 질문 검색. 유사도 0.88 이상이면 **캐시 히트**로 공식 답변 즉시 반환하고 나머지 단계 생략.
5. **프롬프트 조립** — 시스템 프롬프트(역할·말투) + 과거 대화 히스토리 + 개인 자료 블록(+ 엄격한 8대 답변 원칙 지시문) + 과거 참고 Q&A 블록(있을 때) + 사용자 질문을 하나의 message 배열로 구성.
6. **OpenAI 호출** — gpt-4o-mini에 temperature=0으로 요청해 동일 질문에는 거의 동일한 답을 받도록 함.
7. **응답 후처리 — 잡담/무관 여부 판별** — "자료에 해당 정보가 없습니다" 등 "자료 없음" 패턴이나 "안녕하세요" 등 잡담 패턴이 감지되면 ChatLog 저장을 생략하고 sources도 비워 UI에 출처 뱃지·피드백 버튼이 뜨지 않게 함.
8. **ChatLog 저장** — 자료 기반 답변일 때만 저장. 저장 시 유사도 0.90 이상의 기존 ChatLog가 있으면 재사용해 피드백이 흩어지지 않게 함.
9. **TokenUsage 기록** — 호출한 모델명·입력/출력/총 토큰 수를 대시보드용 로그로 적재.
10. **응답 반환** — 답변 텍스트 + 출처 리스트(문서명·URL) + chat_log_id를 JSON으로 프런트에 전송.

**결정성·일관성 확보 포인트**
- 답변 생성과 재정렬 모두 `temperature=0`.
- 유사 질문은 ChatLog에서 같은 레코드로 병합 → 피드백·BO 관리 대상이 명확.
- 관리자가 승격한 공식 답변은 캐시로 그대로 반환 → 반복 질문의 답변이 결코 흔들리지 않음.

---

## 8. 사용자 피드백 & 관리자 검수 루프

**피드백 수집**
- 답변 하단에 👍/👎 버튼(자료 기반 답변일 때만 노출).
- 클릭 시 서버에 `{chat_log_id, rating}` 전송. 브라우저 `localStorage`로 세션 내 중복 방지.
- `Feedback` 레코드가 ChatLog에 누적.

**관리자 검수**
- BO 대화 로그: 미승격 필터 기본. "공식 승격" 버튼으로 CanonicalQA 생성.
- BO 답변 응답: 피드백 분포에 따른 세 탭(전체·좋음·나쁨). 👎가 많으면 답변 품질 점검 대상.
- BO 공식 Q&A: 승격된 답변을 편집·삭제. 편집 시 질문 임베딩도 재계산.

**승격의 의미**
- CanonicalQA에 들어간 답변은 이후 RAG 검색의 후보가 되며, 유사도 0.88 이상이면 OpenAI 호출 없이 그대로 사용됨.
- 동일 ChatLog 재승격은 방지 — 기존 CanonicalQA가 있으면 새로 만들지 않음.

---

## 9. 전체 동작 로직

**관리자 관점 — 지식 베이스 구축**
1. BO 파일관리에서 개인 문서를 업로드.
2. 자동으로 검토 페이지로 이동 — PyMuPDF가 추출한 텍스트를 검토.
3. 표·조항·숫자가 잘못 파편화된 부분을 수동 보정.
4. "임베딩 진행"을 눌러 청킹·임베딩·저장까지 완료.
5. 운영 중에는 BO 대화 로그·답변 응답에서 Q&A 품질을 지속 검수.
6. 품질 좋은 답변은 공식 Q&A로 승격 → 반복 질문의 답변 일관성·속도 확보.

**사용자 관점 — 질문하기**
1. 채팅 페이지에 질문 입력.
2. 서버가 질문을 관련 공식 Q&A 또는 문서 청크에 매칭.
3. 매칭된 자료를 바탕으로 답변 생성(또는 공식 답변 그대로 반환).
4. 답변 말풍선 아래 참조한 파일 뱃지가 뜸 — 클릭 시 원본 PDF가 모달로 열림.
5. 👍/👎로 답변 품질을 신고.
6. "초기화" 버튼으로 대화 히스토리 리셋.

**시스템 내부 — 한 번의 질문이 만드는 것**
- 벡터 검색 1회 + 키워드 검색 1회 + GPT 재정렬 호출 1회 + (캐시 미스 시) GPT 답변 생성 1회.
- 자료 기반 답변이면 ChatLog·Feedback 가능 상태 + TokenUsage 로그.
- 각 OpenAI 호출은 TokenUsage에 기록되어 대시보드에서 일별로 집계됨.

---

## 10. 배포·실행

**사전 요건**
- Docker · Docker Compose.
- 프로젝트 루트의 `.env` 파일에 환경변수 설정(샘플은 `env.example.txt`). 민감 정보가 모두 `.env`로 분리돼 있어 저장소 코드에는 하드코딩된 비밀값이 없음.

**필요한 환경변수**

| 변수 | 용도 | 비고 |
|---|---|---|
| `DJANGO_SECRET_KEY` | Django 세션·CSRF 서명 키 | `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`로 생성 |
| `POSTGRES_USER` | DB 사용자명 | 로컬 기본 `postgres` |
| `POSTGRES_PASSWORD` | DB 비밀번호 | **운영 시 반드시 강력한 값으로 교체** |
| `POSTGRES_DB` | DB 이름 | 로컬 기본 `mydb` |
| `OPENAI_API_KEY` | OpenAI API 키 | `sk-proj-...` 형식 |
| `OPENAI_MODEL` | 사용 모델 | 기본 `gpt-4o-mini` |

**실행 순서**
1. `docker compose up -d` — web(Django, 8001) + db(PostgreSQL 17, 5432) 컨테이너 기동.
2. `docker compose exec web python manage.py migrate` — **DB 스키마 생성 (필수)**. 이 명령을 돌리기 전까지는 DB에 테이블이 하나도 없어서 서비스가 정상 동작하지 않음.
3. 브라우저에서 `http://localhost:8001/` — 채팅 화면, `http://localhost:8001/bo/` — 백오피스.

> ⚠️ **DB 테이블은 자동 생성되지 않습니다.** Django는 모델 정의(`models.py`)와 실제 DB 테이블을 마이그레이션 파일을 통해 동기화합니다. 컨테이너만 띄워도 Postgres는 비어있는 상태이므로, `migrate` 명령을 반드시 한 번 실행해야 테이블·인덱스·pgvector 확장이 모두 만들어집니다.

**최초 셋업 체크리스트**
1. `env.example.txt`을 `.env`로 복사.
2. `.env`의 각 값을 채움 (`DJANGO_SECRET_KEY` 생성, `POSTGRES_PASSWORD` 설정, `OPENAI_API_KEY` 발급 후 입력).
3. `docker compose up -d` — 컨테이너 기동.
4. `docker compose exec web python manage.py migrate` — DB 스키마 생성 (최초 1회, 필수).
5. (선택) 챗봇 아이콘 이미지를 `resources/icon/icon.png`에 배치.
6. BO 파일관리에서 개인 문서 업로드 시작.

**마이그레이션을 다시 실행해야 할 때**
- 모델(`*/models.py`)을 수정한 뒤 `docker compose exec web python manage.py makemigrations` → `migrate`.
- DB 볼륨을 초기화(`docker compose down -v`)한 뒤에는 다시 `migrate`로 스키마 재구축 필요.
- 첫 `migrate`가 `pgvector` 확장, `pg_trgm` 확장, 4개 앱의 테이블, HNSW·GIN 인덱스를 모두 생성.

**주요 디렉토리**
- `chat/`, `files/`, `bo/`, `AI_Chat/` — Django 앱·설정.
- `chat/static/chat/` · `bo/static/bo/` — 클라이언트 자원(CSS/JS).
- `chat/templates/` · `bo/templates/` — 서버 렌더링 템플릿.
- `resources/` — 런타임 자원. 디렉토리 구조는 Git에 포함(`.gitkeep`), 내부 파일은 제외. 아래 참조.

---

### 10-1. `resources/` 디렉토리 구성

Django의 `MEDIA_ROOT`로 지정되어 있어 `/media/` URL로 서빙됩니다. **디렉토리 구조는 `.gitkeep`으로 Git에 포함돼 있으므로 클론하면 `resources/origin/`, `resources/icon/`이 빈 상태로 이미 존재**합니다. 내부 파일(PDF·이미지 등)은 `.gitignore`로 제외되어 있어 커밋 대상이 아님.

**구조**
```
resources/
├── origin/      ← 관리자가 업로드한 원본 파일 (PDF/DOCX/TXT)
│   └── .gitkeep
└── icon/
    ├── .gitkeep
    └── icon.png ← 챗봇 프로필 아이콘 (선택, 직접 배치)
```

**각 하위 디렉토리 용도**

**`resources/origin/`** — 업로드된 개인 자료 원본
- BO 파일관리에서 업로드 시 Django가 자동으로 파일을 이 디렉토리에 저장.
- 메타데이터는 `files_document` 테이블, 벡터는 `files_documentchunk` 테이블.
- URL로 접근 시 `/media/origin/<파일명>` (예: 출처 뱃지 클릭 시).
- 수동으로 파일을 넣거나 빼지 말 것. 반드시 BO에서 업로드·삭제.

**`resources/icon/icon.png`** — 챗봇 프로필 아이콘
- 채팅 화면에서 봇 메시지 왼쪽에 원형 아이콘으로 표시됨.
- 권장 크기: 64×64 또는 128×128 PNG (원형 크롭되므로 정사각 권장).
- 파일이 없어도 동작은 정상 — 아이콘 자리에 빈 회색 원만 보임. 브라우저 콘솔에 404 경고가 뜰 수 있음.
- 이미지를 나중에 추가하려면 파일만 해당 경로에 복사한 뒤 브라우저 강제 새로고침(Cmd+Shift+R).
- 이 저장소에는 기본 아이콘이 포함돼 있지 않으므로 각자 조직 로고·이미지를 직접 배치.

**별도 초기화 명령 불필요**
- 클론 직후 바로 사용 가능.
- `origin/`은 비어있어도 문제없음 — BO 업로드 시 Django가 자동으로 저장한다.
- `icon/icon.png`은 조직 로고가 있을 때만 넣으면 됨.

---

### 10-2. 데이터베이스 접속 정보

DB는 Docker Compose가 자동으로 기동·연결하므로 **기본 사용엔 직접 접속 불필요**합니다. 다만 DataGrip 같은 GUI 툴이나 `psql`로 직접 접속·디버깅하고 싶을 때 참고.

**접속 정보**

| 항목 | 값 |
|---|---|
| Host | `localhost` (같은 머신) 또는 LAN IP (다른 PC에서) |
| Port | `5432` |
| Database | `.env`의 `POSTGRES_DB` |
| User | `.env`의 `POSTGRES_USER` |
| Password | `.env`의 `POSTGRES_PASSWORD` |
| JDBC URL | `jdbc:postgresql://localhost:5432/<POSTGRES_DB>` |

접속 값은 전부 `.env`에서 읽어 오도록 `docker-compose.yml`이 `${POSTGRES_USER}` 등 변수 치환으로 구성되어 있음. 저장소 코드에는 비밀 값이 남지 않음.

**CLI로 접속**
- 컨테이너 내부에서: `docker compose exec db psql -U $POSTGRES_USER -d $POSTGRES_DB` (또는 `.env` 값 직접 기입)
- 호스트에 psql 설치되어 있으면: `psql -h localhost -p 5432 -U <user> -d <db>`

**pgvector 확장**
- 첫 마이그레이션 시 `VectorExtension()`이 자동으로 `CREATE EXTENSION vector`를 수행.
- pg_trgm은 `files.0003_trigram_search` 마이그레이션이 설치.
- 이미 설치된 `pgvector/pgvector:pg17` 이미지라 수동 설치는 불필요.

**주의 사항**
- 운영 환경으로 옮길 때는 `DATABASES` 설정과 DB 비밀번호를 반드시 변경.
- 컨테이너 볼륨(`ai_chat_db`)이 영속 스토리지이므로, 컨테이너 삭제 시에도 데이터는 유지됨. 완전 초기화는 `docker compose down -v`.

---

### 10-3. 확장 포인트

- 임베딩 모델 교체: `files/services/embedder.py`와 모델 `EMBEDDING_DIM` 상수만 변경 후 전체 재임베딩.
- 검색 임계값·캐시 기준 튜닝: `chat/services/query_pipeline.py` 상단 상수.
- 허용 파일 형식 확장: `files/services/extractor.py`에 새 `_extract_xxx` 함수 추가 + `bo/views/files.py::ALLOWED_EXTS` 갱신.
- 프롬프트 문구 수정: `assets/prompts/chat/*.md` 를 직접 편집하거나, BO `Prompt 관리` 페이지(`/bo/prompts/`)에서 수정. 운영에서는 `PROMPTS_DIR` 환경변수로 외부 마운트 경로에 프롬프트를 두면 재배포 없이 교체 가능.
- 채팅 실행 경로 on/off: `USE_LANGGRAPH_PIPELINE` 환경변수. 기본 `True` 로 LangGraph 기반 `chat/graph/app.run_chat_graph` 를 타고, 문제 시 `False` 로 재기동하면 기존 `answer_question()` direct 호출 경로로 즉시 회귀 가능.

---

### 11. 개발 로그

|     일자      | 내용                 | 파일  |
|:-----------:|:-------------------|:---:|
| 2026.04.22  | 'init' 작업          |  -  |
|2026.04.22| xls 파일 지원 및 편의성 변경 |[2026-04-22.md](resources/documents/2026-04-22.md)|
|2026.04.22| 프로덕션 배포 파이프라인 구축<br>태그 푸시 시 GitHub Actions → GHCR → 운영 호스트 자동 배포 |[2026-04-22-deploy.md](resources/documents/2026-04-22-deploy.md)|
|2026.04.23| 프로덕션 배포 핫픽스 시리즈<br>v0.3.1~v0.3.3: SSH PATH · 컨테이너 GID · /media/ 서빙 |[2026-04-23-deploy-hotfixes.md](resources/documents/2026-04-23-deploy-hotfixes.md)|
|2026.04.23| 2.0.0 Phase 1 — Prompt 관리 분리<br>프롬프트를 파일로 외부화 + Prompt Loader + BO 편집 페이지 |[2026-04-23-phase1-prompt.md](resources/documents/2026-04-23-phase1-prompt.md)|
|2026.04.23| 2.0.0 Phase 2 — LangGraph 적용<br>진입점을 `router → single_shot` 그래프로 전환 + `USE_LANGGRAPH_PIPELINE` 플래그 |[2026-04-23-phase2-langgraph.md](resources/documents/2026-04-23-phase2-langgraph.md)|
|2026.04.23| 2.0.0 Phase 3 — 기존 코드 개편<br>`services/single_shot/` 분해 + `query_pipeline` 제거 + flag 제거 + workflows/ 골격 |[2026-04-23-phase3-refactor.md](resources/documents/2026-04-23-phase3-refactor.md)|
|2026.04.23| 2.0.0 Phase 4-1 — 질문 라우팅 Core<br>규칙 기반 `question_router` + 3 route state · conditional edge 확장 (workflow/agent 는 single_shot 포워딩) |[2026-04-23-phase4-1-router.md](resources/documents/2026-04-23-phase4-1-router.md)|
|2026.04.24| 2.0.0 Phase 4-2 — BO Router Rule 관리<br>`RouterRule` 모델 + BO CRUD + DB 우선 / 코드 키워드 fallback 라우터 |[2026-04-24-phase4-2-router-rules.md](resources/documents/2026-04-24-phase4-2-router-rules.md)|
|2026.04.24| 2.0.0 Phase 4-3 — Retrieval Contextualization<br>retrieval 앞단 `query_rewriter` 삽입 — 후속 질문 "비싼거" 류가 직전 대화 맥락을 반영해 검색되도록 |[2026-04-24-phase4-3-retrieval-context.md](resources/documents/2026-04-24-phase4-3-retrieval-context.md)|
|2026.04.24| 2.0.0 Phase 4-4 — OpenAI 사용량 위젯<br>대시보드 타이틀 우측 `API 사용량` 버튼 + 전체 누적 / 최근 7일 / 모델별 분해 모달 (Admin API 기반) |[2026-04-24-phase4-4-openai-usage.md](resources/documents/2026-04-24-phase4-4-openai-usage.md)|
|2026.04.24| 2.0.0 Phase 5 — Workflow Core<br>`chat/workflows/core/` 6 모듈(result / validation / dates / numbers / formatting / base) + `BaseWorkflow` Protocol + `run_workflow` 러너. Phase 6 도메인 workflow 의 공용 엔진 |[2026-04-24-phase5-workflow-core.md](resources/documents/2026-04-24-phase5-workflow-core.md)|

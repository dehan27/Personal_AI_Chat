"""BO 에서 편집 가능한 프롬프트 파일의 allow-list.

BO 에서 URL 로 받은 key 로만 파일에 접근하게 해서, 사용자가 임의 상대 경로를
넘기거나 path traversal 로 PROMPTS_DIR 밖을 건드리는 시나리오를 원천 차단한다.

Phase 1 은 코드 상수로 유지한다. 이후 phase 에서 router/workflow/agent 프롬프트가
늘어나면 같은 자료 구조에 항목만 추가하면 된다.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptEntry:
    """BO 에서 노출·편집할 프롬프트 한 건."""

    key: str              # URL 슬러그로 사용. 소문자 + 하이픈만 권장
    title: str            # BO 목록·편집 화면 상단에 표시
    description: str      # 역할·주의사항을 간단히
    relative_path: str    # PROMPTS_DIR 기준 상대 경로
    editable: bool = True


# 순서는 BO 목록 노출 순서와 같다.
PROMPT_REGISTRY: list[PromptEntry] = [
    PromptEntry(
        key='chat-system',
        title='채팅 시스템 프롬프트',
        description='챗봇의 기본 말투·답변 원칙을 정의한다. 모든 채팅 요청의 첫 번째 system 메시지로 붙는다.',
        relative_path='chat/system.md',
    ),
    PromptEntry(
        key='chat-source-instruction',
        title='회사 자료 답변 지시문',
        description='검색된 회사 자료를 LLM에 넘길 때 함께 붙는 규칙. 수치 보존·출처·환각 금지 등.',
        relative_path='chat/source_instruction.md',
    ),
    PromptEntry(
        key='chat-qa-instruction',
        title='과거 참고 답변 지시문',
        description='공식 Q&A 검색 결과를 참고 자료로 넘길 때 붙는 규칙. 톤 참고용으로만 쓰도록 유도.',
        relative_path='chat/qa_instruction.md',
    ),
    PromptEntry(
        key='chat-no-sources-guard',
        title='자료 없음 가드',
        description='검색 결과가 전혀 없을 때 외부 지식으로 답하지 못하게 막는 가드 문구.',
        relative_path='chat/no_sources_guard.md',
    ),
    PromptEntry(
        key='chat-query-rewriter',
        title='검색어 재작성 프롬프트',
        description='후속 질문이 "비싼거" 처럼 맥락에 의존할 때, 직전 대화를 반영해 자립 검색어로 바꾸는 지시문. retrieval 앞단에서만 사용되며 최종 답변에는 영향을 주지 않는다.',
        relative_path='chat/query_rewriter.md',
    ),
    PromptEntry(
        key='chat-workflow-input-extractor',
        title='Workflow 입력 추출 프롬프트',
        description='workflow 경로에서 regex 로 채우지 못한 필수 입력 값을 LLM 이 JSON 으로 채워넣을 때 쓰는 지시문. Phase 6-2 에서 도입.',
        relative_path='chat/workflow_input_extractor.md',
    ),
    PromptEntry(
        key='chat-table-lookup',
        title='표 조회 프롬프트',
        description='table_lookup workflow 가 retrieval 로 확보한 마크다운 표 중에서 사용자가 묻는 셀 하나를 JSON 으로 집어 내도록 지시. Phase 6-3.',
        relative_path='chat/table_lookup.md',
    ),
    PromptEntry(
        key='chat-agent-react',
        title='Agent ReAct 시스템 프롬프트',
        description='generic agent 의 ReAct loop 가 매 step JSON 한 줄로 다음 action 또는 final_answer 를 결정하도록 지시. Phase 7-1.',
        relative_path='chat/agent_react.md',
    ),
]

# O(1) 조회용
_BY_KEY: dict[str, PromptEntry] = {entry.key: entry for entry in PROMPT_REGISTRY}


def all_entries() -> list[PromptEntry]:
    """BO 목록에 렌더링할 순서대로 반환."""
    return list(PROMPT_REGISTRY)


def get_entry(key: str) -> PromptEntry | None:
    """key 로 단건 조회. 없으면 None (BO 쪽에서 404로 변환)."""
    return _BY_KEY.get(key)

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
]

# O(1) 조회용
_BY_KEY: dict[str, PromptEntry] = {entry.key: entry for entry in PROMPT_REGISTRY}


def all_entries() -> list[PromptEntry]:
    """BO 목록에 렌더링할 순서대로 반환."""
    return list(PROMPT_REGISTRY)


def get_entry(key: str) -> PromptEntry | None:
    """key 로 단건 조회. 없으면 None (BO 쪽에서 404로 변환)."""
    return _BY_KEY.get(key)

"""OpenAI에 보낼 메시지 리스트를 조립하는 모듈.

입력:
- 현재 질문
- DocumentChunk 검색 결과 (회사 자료)
- QAPair 검색 결과 (과거 참고 답변)
- 세션 대화 히스토리

출력:
- OpenAI chat.completions.create()에 바로 넘길 수 있는 messages 리스트
"""

from typing import List, Dict, Any

from chat.prompt.chat import SYSTEM_PROMPT
from chat.services.qa_retriever import QAHit
from files.services.retriever import ChunkHit


# RAG 컨텍스트를 user 메시지에 감쌀 때 쓰는 지시문
_SOURCES_INSTRUCTION = (
    '다음은 질문에 관련해 검색된 회사 자료이다.\n'
    '아래 원칙을 엄격히 지켜서 답하라.\n'
    '질문이 짧거나 모호해도 자료에 있는 관련 정보를 종합해서 제공한다.\n'
    '1. 자료에 관련 내용이 있으면 그것을 바탕으로 자세히 답한다. '
    '2. 수치·일수·금액·기간 등 구체적인 값은 반드시 자료에 적힌 그대로 인용한다. 추정·반올림·변형 금지.\n'
    '3. 자료에 없는 수치는 절대 만들어내지 말 것. 없으면 "자료에 명시되지 않음"이라고 쓴다.\n'
    '4. 자료들 사이에 값이 충돌하면 모든 값을 함께 제시한다.\n'
    '5. 자료가 질문 주제와 전혀 무관할 때만 "회사 자료에 해당 정보가 없습니다"라고 답한다.\n'
    '6. 일반 상식·외부 지식 사용 금지. 오직 제공된 자료만 사용.\n'
    '7. 자료와 과거 참고 답변이 충돌하면 자료를 따른다.\n'
    '출처는 별도 UI에서 표시되므로 본문에는 필요 없다.'
)

_QA_INSTRUCTION = (
    '다음은 과거에 비슷한 질문에 답변한 기록이다.\n'
    '답변 톤·일관성 참고 용도로만 사용하고, 현재 질문과 완전히 같지 않으면 그대로 모방하지 말라.'
)

# 검색 결과가 전혀 없을 때의 가드 지시문
_NO_SOURCES_GUARD = (
    '주의: 이번 질문에 회사 자료에서 관련 내용을 찾지 못했다.\n'
    '일반 지식이나 상식으로 답하지 말고, 반드시 "회사 자료에 해당 정보가 없습니다"라고만 답해라.\n'
    '이 챗봇은 회사 자료 기반으로만 답변하며, 외부 지식은 제공하지 않는다.'
)


def build_messages(
    question: str,
    chunk_hits: List[ChunkHit],
    qa_hits: List[QAHit],
    history: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """OpenAI 호환 messages 리스트를 만든다.

    Args:
        question: 사용자 질문 (raw, 가공 안 된 원문)
        chunk_hits: DocumentChunk 검색 결과
        qa_hits: QAPair 검색 결과
        history: 세션 히스토리 [{'role': ..., 'content': ...}, ...]

    Returns:
        OpenAI API에 바로 넘길 수 있는 메시지 리스트
    """
    messages: List[Dict[str, Any]] = []

    # ① 시스템 프롬프트 (역할·말투 규칙)
    messages.append({'role': 'system', 'content': SYSTEM_PROMPT})

    # ② 과거 대화 히스토리 그대로 이어붙임
    messages.extend(history)

    # ③ 이번 turn의 user 메시지: 자료 + 과거참고 + 질문
    user_content = _render_user_content(question, chunk_hits, qa_hits)
    messages.append({'role': 'user', 'content': user_content})

    return messages


def _render_user_content(
    question: str,
    chunk_hits: List[ChunkHit],
    qa_hits: List[QAHit],
) -> str:
    """현재 turn의 user 메시지 본문을 조립."""
    sections: List[str] = []

    # 회사 자료 섹션 (청크가 있을 때만)
    if chunk_hits:
        sections.append('=== 회사 자료 ===')
        sections.append(_SOURCES_INSTRUCTION)
        sections.append('')
        for i, hit in enumerate(chunk_hits, 1):
            sections.append(hit.content)
            sections.append('')
    else:
        # 검색 결과 없음 — 일반 지식 답변 차단 가드
        sections.append('=== 중요 ===')
        sections.append(_NO_SOURCES_GUARD)
        sections.append('')

    # 과거 참고 답변 섹션 (QAPair가 있을 때만)
    if qa_hits:
        sections.append('=== 과거 참고 답변 ===')
        sections.append(_QA_INSTRUCTION)
        sections.append('')
        for hit in qa_hits:
            sections.append(f'Q: {hit.question}')
            sections.append(f'A: {hit.answer}')
            sections.append('')

    # 이번 질문
    sections.append('=== 사용자 질문 ===')
    sections.append(question)

    return '\n'.join(sections)

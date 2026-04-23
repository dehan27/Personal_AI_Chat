"""단계 5: single-shot 용 프롬프트 조립.

지금은 Phase 1 에서 안정화된 `chat.services.prompt_builder.build_messages` 를
얇게 감싸기만 한다. workflow / agent 전용 builder 가 추가될 때 이 래퍼 위치가
확장 지점이 된다.
"""

from typing import Any, Dict, List

from chat.services.prompt_builder import build_messages
from chat.services.qa_retriever import QAHit
from files.services.retriever import ChunkHit


def build_single_shot_messages(
    question: str,
    chunk_hits: List[ChunkHit],
    qa_hits: List[QAHit],
    history: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """OpenAI chat.completions 에 바로 넘길 messages 리스트를 돌려준다."""
    return build_messages(question, chunk_hits, qa_hits, history)

"""TokenUsage.purpose 의 코드 상수 (Phase 8-2).

`record_token_usage(model, usage, *, purpose=...)` 가 받는 문자열 후보의 single
source of truth. CharField + 코드 상수 정책 — DB enum 강제 X (Phase 8 설계 §6-5
권장: "DB 에는 문자열 필드, 선택지는 코드 상수"). `validate_purpose` 방어망이
record 진입 시 알 수 없는 값을 `PURPOSE_UNKNOWN` 으로 절감해 호출부 오타가
데이터 오염으로 직결되지 않게.

새 purpose 추가 시 검토 항목:
1. 본 모듈에 `PURPOSE_<NAME>` 상수 추가.
2. `ALL_PURPOSES` set 에 포함되는지 확인 (자동 — 모듈 끝 frozenset 빌더가 처리).
3. 호출 사이트에서 `purpose=PURPOSE_<NAME>` 명시.
4. `chat/tests/test_token_purpose.py` 의 멤버십 테스트 업데이트 (상수 추가 시).
"""

from __future__ import annotations

import logging
from typing import FrozenSet


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Known purposes — Phase 8-2 진입 시점의 7 호출 사이트 매핑.
# ---------------------------------------------------------------------------

# single_shot 답변 LLM (`chat/services/single_shot/pipeline.py:70`).
PURPOSE_SINGLE_SHOT_ANSWER = 'single_shot_answer'

# query rewriter (`chat/services/query_rewriter.py` 의 LLM 호출). single_shot /
# workflow / agent 세 경로가 같은 함수를 공유하므로 purpose 도 단일.
PURPOSE_QUERY_REWRITER = 'query_rewriter'

# workflow input extractor (`chat/services/workflow_input_extractor.py`).
# `graph/nodes/workflow.py:73` 에서 record.
PURPOSE_WORKFLOW_EXTRACTOR = 'workflow_extractor'

# table_lookup 의 셀 선택 LLM (`chat/workflows/domains/general/table_lookup.py:150`).
PURPOSE_WORKFLOW_TABLE_LOOKUP = 'workflow_table_lookup'

# agent ReAct loop 의 도구 선택 / 추론 LLM 호출
# (`chat/services/agent/react.py:101`, action != 'final_answer' 인 step).
PURPOSE_AGENT_STEP = 'agent_step'

# agent ReAct loop 의 final_answer iteration LLM 호출
# (`chat/services/agent/react.py:101`, action == 'final_answer' 인 step).
PURPOSE_AGENT_FINAL = 'agent_final'

# 누락 / 외부 호출 / 마이그레이션 이전 row 의 default 분류.
PURPOSE_UNKNOWN = 'unknown'


# ---------------------------------------------------------------------------
# 멤버십 / 검증 helper.
# ---------------------------------------------------------------------------

ALL_PURPOSES: FrozenSet[str] = frozenset({
    PURPOSE_SINGLE_SHOT_ANSWER,
    PURPOSE_QUERY_REWRITER,
    PURPOSE_WORKFLOW_EXTRACTOR,
    PURPOSE_WORKFLOW_TABLE_LOOKUP,
    PURPOSE_AGENT_STEP,
    PURPOSE_AGENT_FINAL,
    PURPOSE_UNKNOWN,
})


def validate_purpose(value: str) -> str:
    """`purpose` 가 알려진 값이면 그대로, 아니면 `PURPOSE_UNKNOWN` 으로 절감.

    호출부 오타 / 외부 import 가 모르는 값을 가져왔을 때 데이터 오염 차단용 방어망.
    `record_token_usage` 가 진입 직후 한 번 호출.
    """
    if value in ALL_PURPOSES:
        return value
    logger.warning(
        "validate_purpose: 알 수 없는 purpose=%r → 'unknown' 으로 절감", value,
    )
    return PURPOSE_UNKNOWN

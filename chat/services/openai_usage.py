"""OpenAI 조직 사용량 집계 (Phase 4-4).

BO 대시보드 모달이 쓰는 유일한 진입점은 `fetch_usage_summary()` 하나다.
이 함수는 OpenAI Admin API 의 세 엔드포인트를 호출해 전체 누적·최근 7일·
모델별 분해를 모달 친화 JSON 으로 돌려준다.

엔드포인트 (`Authorization: Bearer sk-admin-...`):
    GET /v1/organization/usage/completions
    GET /v1/organization/usage/embeddings
    GET /v1/organization/costs

Admin 키는 일반 `OPENAI_API_KEY` 와 다르다 — 조직 Owner 가 별도로 발급한
`sk-admin-...` 를 `.env` 의 `OPENAI_ADMIN_KEY` 로 주입해야 한다. 키가 없으면
`AdminKeyMissing` 을 올려 BO 엔드포인트에서 503 + 친절한 에러 메시지로 변환.

외부 HTTP 는 stdlib `urllib.request` 로 호출 — requests 를 명시적 의존성으로
추가하지 않기 위함.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


logger = logging.getLogger(__name__)


# OpenAI Usage API 가 보유한 데이터 시점 + 실제 운영 시작 지점.
# 그 이전은 어차피 빈 결과라 `전체 누적` 기준을 여기에 고정한다.
CUMULATIVE_START = datetime(2026, 1, 1, tzinfo=timezone.utc)

# 모달 하단 테이블에 쓰는 단기 윈도우.
RECENT_DAYS = 7

# 상위 호출에서 기다릴 수 있는 최대 시간 (초).
REQUEST_TIMEOUT = 10

# 페이지네이션 안전판. OpenAI 가 bucket_width=1d 에서 페이지당 최대 31 일(=31 버킷)
# 만 허용하므로 1 년치를 뽑아도 ~12 페이지면 끝난다. 여유롭게 24 페이지.
MAX_PAGES = 24

# bucket_width=1d 에서 허용되는 페이지당 최대 개수. 1m=1440, 1h=168, 1d=31 제한.
_BUCKETS_PER_PAGE = 31

_BASE_URL = 'https://api.openai.com/v1/organization'


class AdminKeyMissing(RuntimeError):
    """`OPENAI_ADMIN_KEY` 가 .env / 환경에 설정되지 않음."""


class UsageAPIError(RuntimeError):
    """OpenAI 사용량 API 호출이 비정상적으로 끝났을 때."""

    def __init__(self, code: str, message: str, *, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def fetch_usage_summary(now: Optional[datetime] = None) -> Dict[str, Any]:
    """모달이 그대로 렌더할 수 있는 집계 JSON 을 반환.

    반환 구조:
        {
            "total":   {input_tokens, output_tokens, total_tokens, cost_usd},
            "last_7d": {input_tokens, output_tokens, total_tokens, cost_usd,
                        daily: [{date, input, output, total, cost_usd}],
                        by_model: [{model, tokens, cost_usd}]},
        }
    """
    admin_key = os.environ.get('OPENAI_ADMIN_KEY', '').strip()
    if not admin_key:
        raise AdminKeyMissing('OPENAI_ADMIN_KEY 가 설정되지 않았습니다.')

    now = now or datetime.now(timezone.utc)
    recent_start = (now - timedelta(days=RECENT_DAYS - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )

    total = _aggregate_totals(admin_key, CUMULATIVE_START, now)
    recent = _aggregate_totals(admin_key, recent_start, now)
    daily = _collect_daily(admin_key, recent_start, now)
    by_model = _collect_by_model(admin_key, recent_start, now)

    return {
        'total': total,
        'last_7d': {
            **recent,
            'daily': daily,
            'by_model': by_model,
        },
    }


# ---------------------------------------------------------------------------
# 집계 헬퍼
# ---------------------------------------------------------------------------

def _aggregate_totals(
    admin_key: str,
    start: datetime,
    end: datetime,
) -> Dict[str, int]:
    """completions + embeddings + costs 를 더해 합계 한 덩어리를 만든다."""
    in_tok, out_tok = _sum_completions(admin_key, start, end)
    emb_tok = _sum_embeddings(admin_key, start, end)
    cost = _sum_costs(admin_key, start, end)

    # 임베딩은 input 만 있다 — 합산할 때 input 쪽에 더한다.
    input_tokens = in_tok + emb_tok
    total_tokens = input_tokens + out_tok
    return {
        'input_tokens': input_tokens,
        'output_tokens': out_tok,
        'total_tokens': total_tokens,
        'cost_usd': round(cost, 4),
    }


def _collect_daily(
    admin_key: str,
    start: datetime,
    end: datetime,
) -> List[Dict[str, Any]]:
    """최근 7일 일별 행. 날짜 오름차순 (표에서 그대로 렌더)."""
    # 일별 버킷에서 completions/embeddings 토큰을 모은다.
    # 빈 버킷(활동 없는 날)은 기록하지 않고 건너뛴다.
    per_day: Dict[str, Dict[str, float]] = {}

    def _ensure(date_key: str) -> Dict[str, float]:
        return per_day.setdefault(
            date_key,
            {'input': 0, 'output': 0, 'cost_usd': 0.0},
        )

    for bucket in _iter_buckets(admin_key, 'usage/completions', start, end, bucket_width='1d'):
        date_key = _bucket_date(bucket)
        for result in bucket.get('results', []):
            agg = _ensure(date_key)
            agg['input'] += int(result.get('input_tokens', 0) or 0)
            agg['output'] += int(result.get('output_tokens', 0) or 0)

    for bucket in _iter_buckets(admin_key, 'usage/embeddings', start, end, bucket_width='1d'):
        date_key = _bucket_date(bucket)
        for result in bucket.get('results', []):
            agg = _ensure(date_key)
            # 임베딩 응답은 'input_tokens' 또는 'num_input_tokens' 로 내려오는 변형이 있다.
            agg['input'] += int(
                result.get('input_tokens', result.get('num_input_tokens', 0)) or 0,
            )

    for bucket in _iter_buckets(admin_key, 'costs', start, end, bucket_width='1d'):
        date_key = _bucket_date(bucket)
        for result in bucket.get('results', []):
            agg = _ensure(date_key)
            amount = result.get('amount') or {}
            agg['cost_usd'] += float(amount.get('value', 0) or 0)

    rows = []
    for date_key in sorted(per_day.keys()):
        agg = per_day[date_key]
        total = int(agg['input']) + int(agg['output'])
        rows.append({
            'date': date_key,
            'input': int(agg['input']),
            'output': int(agg['output']),
            'total': total,
            'cost_usd': round(agg['cost_usd'], 4),
        })
    return rows


def _collect_by_model(
    admin_key: str,
    start: datetime,
    end: datetime,
) -> List[Dict[str, Any]]:
    """최근 7일 모델별 (토큰 합 + 비용). 토큰 내림차순."""
    # 모델별 토큰: completions 는 group_by=model 사용 가능.
    per_model_tokens: Dict[str, int] = {}
    for bucket in _iter_buckets(
        admin_key,
        'usage/completions',
        start,
        end,
        bucket_width='1d',
        extra_params=[('group_by[]', 'model')],
    ):
        for result in bucket.get('results', []):
            model = result.get('model') or 'unknown'
            tokens = int(result.get('input_tokens', 0) or 0) + int(result.get('output_tokens', 0) or 0)
            per_model_tokens[model] = per_model_tokens.get(model, 0) + tokens

    for bucket in _iter_buckets(
        admin_key,
        'usage/embeddings',
        start,
        end,
        bucket_width='1d',
        extra_params=[('group_by[]', 'model')],
    ):
        for result in bucket.get('results', []):
            model = result.get('model') or 'unknown'
            tokens = int(
                result.get('input_tokens', result.get('num_input_tokens', 0)) or 0,
            )
            per_model_tokens[model] = per_model_tokens.get(model, 0) + tokens

    # 모델별 비용: costs 엔드포인트는 group_by 에 model 을 지원하지 않는 버전이 있어
    # line_item 기반으로만 집계한다 — 여기서는 비용을 모델 단위로 쪼개지 않고 0 으로 둔다.
    # 필요해지면 Phase 4-5 에서 rates 기반 추정 로직 추가.

    rows = [
        {'model': model, 'tokens': tokens, 'cost_usd': 0.0}
        for model, tokens in per_model_tokens.items()
    ]
    rows.sort(key=lambda r: r['tokens'], reverse=True)
    return rows


# ---------------------------------------------------------------------------
# HTTP 호출
# ---------------------------------------------------------------------------

def _sum_completions(
    admin_key: str,
    start: datetime,
    end: datetime,
) -> Tuple[int, int]:
    in_sum, out_sum = 0, 0
    for bucket in _iter_buckets(admin_key, 'usage/completions', start, end, bucket_width='1d'):
        for result in bucket.get('results', []):
            in_sum += int(result.get('input_tokens', 0) or 0)
            out_sum += int(result.get('output_tokens', 0) or 0)
    return in_sum, out_sum


def _sum_embeddings(
    admin_key: str,
    start: datetime,
    end: datetime,
) -> int:
    total = 0
    for bucket in _iter_buckets(admin_key, 'usage/embeddings', start, end, bucket_width='1d'):
        for result in bucket.get('results', []):
            total += int(
                result.get('input_tokens', result.get('num_input_tokens', 0)) or 0,
            )
    return total


def _sum_costs(
    admin_key: str,
    start: datetime,
    end: datetime,
) -> float:
    total = 0.0
    for bucket in _iter_buckets(admin_key, 'costs', start, end, bucket_width='1d'):
        for result in bucket.get('results', []):
            amount = result.get('amount') or {}
            total += float(amount.get('value', 0) or 0)
    return total


def _iter_buckets(
    admin_key: str,
    path: str,
    start: datetime,
    end: datetime,
    *,
    bucket_width: str = '1d',
    extra_params: Optional[List[Tuple[str, str]]] = None,
):
    """OpenAI Usage / Costs 응답의 모든 버킷을 순회 (페이지네이션 투명 처리)."""
    page: Optional[str] = None
    for _ in range(MAX_PAGES):
        params: List[Tuple[str, str]] = [
            ('start_time', str(int(start.timestamp()))),
            ('end_time', str(int(end.timestamp()))),
            ('bucket_width', bucket_width),
            ('limit', str(_BUCKETS_PER_PAGE)),
        ]
        if page:
            params.append(('page', page))
        if extra_params:
            params.extend(extra_params)

        body = _get_json(admin_key, path, params)
        for bucket in body.get('data', []):
            yield bucket

        if not body.get('has_more'):
            return
        page = body.get('next_page')
        if not page:
            return


def _get_json(
    admin_key: str,
    path: str,
    params: List[Tuple[str, str]],
) -> Dict[str, Any]:
    url = f'{_BASE_URL}/{path}?{urlencode(params)}'
    req = Request(url, headers={
        'Authorization': f'Bearer {admin_key}',
        'Accept': 'application/json',
    })
    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode('utf-8')
    except HTTPError as exc:
        status = exc.code
        if status == 401:
            raise UsageAPIError('unauthorized', 'OpenAI Admin 키 인증 실패', status=status) from exc
        logger.warning('OpenAI Usage API HTTP %s for %s', status, path)
        raise UsageAPIError('upstream_failed', f'OpenAI 응답 오류 ({status})', status=status) from exc
    except URLError as exc:
        logger.warning('OpenAI Usage API 네트워크 실패 %s: %s', path, exc)
        raise UsageAPIError('upstream_failed', 'OpenAI 응답을 받지 못했습니다.') from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning('OpenAI Usage API 응답 파싱 실패 %s: %s', path, exc)
        raise UsageAPIError('bad_payload', 'OpenAI 응답을 해석하지 못했습니다.') from exc


def _bucket_date(bucket: Dict[str, Any]) -> str:
    """버킷의 start_time 을 YYYY-MM-DD 로 변환."""
    ts = int(bucket.get('start_time', 0) or 0)
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime('%Y-%m-%d')

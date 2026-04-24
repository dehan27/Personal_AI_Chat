"""OpenAI 사용량 모달용 JSON 엔드포인트 (Phase 4-4).

대시보드 모달이 `GET /bo/api/openai-usage/` 로 호출하면 여기서 Admin 키
기반 집계를 반환한다. 키 부재 / 인증 실패 / 네트워크 오류를 각기 다른
HTTP 상태로 내려 프런트가 구분된 메시지를 띄울 수 있게 한다.

응답 형식:
    200: openai_usage.fetch_usage_summary() 결과 그대로
    503: {"error": "admin_key_missing", "message": "..."}
    502: {"error": "<stable_code>", "message": "..."}  — OpenAI 측 문제
    500: 예상치 못한 오류
"""

import logging

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from chat.services.openai_usage import (
    AdminKeyMissing,
    UsageAPIError,
    fetch_usage_summary,
)


logger = logging.getLogger(__name__)


@require_GET
def openai_usage(request):
    try:
        summary = fetch_usage_summary()
    except AdminKeyMissing as exc:
        return JsonResponse(
            {'error': 'admin_key_missing', 'message': str(exc)},
            status=503,
        )
    except UsageAPIError as exc:
        return JsonResponse(
            {'error': exc.code, 'message': str(exc)},
            status=502,
        )
    except Exception as exc:  # noqa: BLE001 — 예상 못 한 경로도 JSON 으로 감싼다
        logger.exception('OpenAI 사용량 조회 중 예기치 못한 오류')
        return JsonResponse(
            {'error': 'unexpected', 'message': '데이터를 해석하지 못했습니다.'},
            status=500,
        )

    return JsonResponse(summary)

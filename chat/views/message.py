import json

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from chat.graph.app import run_chat_graph
from chat.services.history_service import (
    clear_history, get_history, save_history,
)
from chat.services.query_pipeline import QueryPipelineError, answer_question


@require_http_methods(['POST'])
def message(request):
    # JSON 바디 파싱
    try:
        body = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'error': '잘못된 요청 형식입니다.'}, status=400)

    user_text = (body.get('message') or '').strip()
    if not user_text:
        return JsonResponse({'error': '메시지를 입력하세요.'}, status=400)

    # 세션에서 과거 대화 불러오기 (RAG 컨텍스트와 별개)
    history = get_history(request)

    # RAG 파이프라인 실행.
    # Phase 2: USE_LANGGRAPH_PIPELINE=True 면 graph 진입점, False 면 기존 direct 호출.
    # 두 경로 모두 QueryResult 반환 / QueryPipelineError raise 로 시그니처 동일.
    try:
        if settings.USE_LANGGRAPH_PIPELINE:
            result = run_chat_graph(user_text, history=history)
        else:
            result = answer_question(user_text, history=history)
    except QueryPipelineError as e:
        return JsonResponse({'error': str(e)}, status=502)

    # 이번 턴을 히스토리에 추가 (raw 질문/답변만 저장)
    history.append({'role': 'user', 'content': user_text})
    history.append({'role': 'assistant', 'content': result.reply})
    save_history(request, history)

    return JsonResponse({
        'reply': result.reply,
        'sources': result.sources,
        'chat_log_id': result.chat_log_id,
    })


@require_http_methods(['POST'])
def reset(request):
    # 세션 히스토리 초기화
    clear_history(request)
    return JsonResponse({'ok': True})

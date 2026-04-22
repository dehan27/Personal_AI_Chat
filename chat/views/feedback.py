import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from chat.models import ChatLog, Feedback


@require_http_methods(['POST'])
def feedback(request):
    """사용자 피드백(엄지) 저장.

    Body (JSON):
        { "chat_log_id": <int>, "rating": "up" | "down" }
    """
    try:
        body = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'error': '잘못된 요청 형식입니다.'}, status=400)

    chat_log_id = body.get('chat_log_id')
    rating = body.get('rating')

    if not isinstance(chat_log_id, int):
        return JsonResponse({'error': 'chat_log_id가 필요합니다.'}, status=400)
    if rating not in (Feedback.Rating.UP, Feedback.Rating.DOWN):
        return JsonResponse({'error': 'rating은 up/down 중 하나여야 합니다.'}, status=400)

    cl = get_object_or_404(ChatLog, pk=chat_log_id)
    Feedback.objects.create(chat_log=cl, rating=rating)
    return JsonResponse({'ok': True})

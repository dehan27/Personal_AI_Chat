from chat.services.prompt_loader import load_prompt

# 세션에 저장할 대화 히스토리 키
SESSION_HISTORY_KEY = 'chat_history'

# 대화 히스토리 최대 턴 수 (유저+어시스턴트 합산)
# 너무 길어지면 토큰 비용이 늘고 오래된 맥락이 희석되므로 적당히 자름
MAX_HISTORY_MESSAGES = 20

def initial_history():
    return [{'role': 'system', 'content': load_prompt('chat/system.md')}]


def get_history(request):
    return request.session.get(SESSION_HISTORY_KEY, [])


def save_history(request, history):
    # 최근 N개만 유지 (시스템 프롬프트는 매 요청마다 앞에 붙이므로 히스토리에는 미포함)
    request.session[SESSION_HISTORY_KEY] = history[-MAX_HISTORY_MESSAGES:]
    request.session.modified = True

def clear_history(request):
    request.session[SESSION_HISTORY_KEY] = []
    request.session.modified = True



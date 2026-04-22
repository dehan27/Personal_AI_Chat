from django.shortcuts import render


def home(request):
    # 채팅 메인 페이지
    return render(request, 'chat/index.html')
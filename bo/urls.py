from django.urls import path

from . import views

app_name = 'bo'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),

    # 파일관리
    path('files/', views.files, name='files'),
    path('files/upload/', views.upload, name='upload'),
    path('files/<int:pk>/review/', views.review, name='review'),
    path('files/<int:pk>/confirm/', views.confirm, name='confirm'),
    path('files/<int:pk>/delete/', views.delete, name='delete'),

    # Q&A 관리 (세 섹션: 대화 로그 / 답변 응답 / 공식 Q&A)
    path('qa/', views.qa_root, name='qa_root'),
    path('qa/logs/', views.qa_logs, name='qa_logs'),
    path('qa/feedback/', views.qa_feedback, name='qa_feedback'),
    path('qa/canonical/', views.qa_canonical, name='qa_canonical'),

    # 액션
    path('qa/logs/<int:pk>/promote/', views.qa_promote, name='qa_promote'),
    path('qa/logs/<int:pk>/delete/', views.qa_log_delete, name='qa_log_delete'),
    path('qa/canonical/<int:pk>/update/', views.qa_canonical_update, name='qa_canonical_update'),
    path('qa/canonical/<int:pk>/delete/', views.qa_canonical_delete, name='qa_canonical_delete'),

    # Prompt 관리 (registry 기반 allow-list 편집기)
    path('prompts/', views.prompts_index, name='prompts'),
    path('prompts/<slug:key>/', views.prompts_edit, name='prompts_edit'),
    path('prompts/<slug:key>/update/', views.prompts_update, name='prompts_update'),
]

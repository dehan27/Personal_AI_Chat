from django.urls import path

from . import views

app_name = 'bo'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),

    # 대시보드 모달이 fetch 하는 OpenAI 사용량 JSON 엔드포인트 (Phase 4-4)
    path('api/openai-usage/', views.openai_usage, name='openai_usage'),

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

    # 일괄 액션 (bulk) — ids[] POST 로 받음
    path('qa/logs/bulk-promote/', views.qa_bulk_promote, name='qa_bulk_promote'),
    path('qa/logs/bulk-delete/', views.qa_bulk_delete_logs, name='qa_bulk_delete_logs'),
    path('qa/canonical/bulk-delete/', views.qa_bulk_delete_canonical, name='qa_bulk_delete_canonical'),

    # Prompt 관리 (registry 기반 allow-list 편집기)
    path('prompts/', views.prompts_index, name='prompts'),
    path('prompts/<slug:key>/', views.prompts_edit, name='prompts_edit'),
    path('prompts/<slug:key>/update/', views.prompts_update, name='prompts_update'),

    # 라우팅 관리 (Phase 4-2 RouterRule CRUD)
    path('router-rules/', views.router_rules_index, name='router_rules'),
    path('router-rules/new/', views.router_rules_new, name='router_rules_new'),
    path('router-rules/<int:pk>/edit/', views.router_rules_edit, name='router_rules_edit'),
    path('router-rules/<int:pk>/toggle/', views.router_rules_toggle, name='router_rules_toggle'),
    path('router-rules/<int:pk>/delete/', views.router_rules_delete, name='router_rules_delete'),
]

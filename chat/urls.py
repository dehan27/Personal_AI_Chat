from django.urls import path

from . import views

app_name = 'chat'

urlpatterns = [
    path('', views.home, name='home'),
    path('message/', views.message, name='message'),
    path('reset/', views.reset, name='reset'),
    path('feedback/', views.feedback, name='feedback'),
]

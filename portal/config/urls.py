from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

from nucleo import views

urlpatterns = [
    path("admin/", admin.site.urls),
    # Chamados pelo Jasmin (rede interna); o token na rota impede chamadas de fora.
    path("interno/jasmin/<str:token>/dlr/", views.dlr, name="interno_dlr"),
    path("interno/jasmin/<str:token>/mo/", views.mo, name="interno_mo"),
    # Por enquanto só existe a gestão TIP; a área do provedor entra depois.
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
]

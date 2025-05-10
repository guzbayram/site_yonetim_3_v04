# yonetim/urls.py
from django.urls import path
from . import views

from django.conf import settings
from django.conf.urls.static import static

app_name = 'yonetim'

urlpatterns = [
    path('', views.giris, name='giris'),
    path('kayit/', views.kayit, name='kayit'),
    path('site-bilgi/', views.site_bilgi, name='site_bilgi'),
    path('panel/', views.panel, name='panel'),
    path('cikis/', views.cikis, name='cikis'),
    path('ajax/bloklar/', views.ajax_bloklar, name='ajax_bloklar'),
    path('ajax/daireler/', views.ajax_daireler, name='ajax_daireler'),
    path('odeme-detay/daire/<int:daire_id>/', views.daire_odeme_detay, name='daire_odeme_detay'),

    # Gider (Expense) için yeni URL'ler
    path('gider/<int:gider_id>/duzenle/', views.gider_update, name='gider_update'),
    path('gider/<int:gider_id>/sil/', views.gider_delete, name='gider_delete'),

    # Aidat (Dues) için yeni URL'ler
    path('aidat/<int:aidat_id>/duzenle/', views.aidat_update, name='aidat_update'),
    path('aidat/<int:aidat_id>/sil/', views.aidat_delete, name='aidat_delete'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
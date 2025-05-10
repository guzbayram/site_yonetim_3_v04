from django.contrib import admin
from .models import Kullanici, Site, Blok, Daire, Aidat, Gider

# Modelleri admin paneline ekle / Register models to admin panel
admin.site.register(Kullanici)
admin.site.register(Site)
admin.site.register(Blok)
admin.site.register(Daire)
admin.site.register(Aidat)
admin.site.register(Gider)

# yonetim/forms.py
from django import forms
from .models import Aidat, Gider
from django.utils.translation import gettext_lazy as _

class AidatForm(forms.ModelForm):
    class Meta:
        model = Aidat
        fields = ['tutar', 'tarih', 'aciklama', 'makbuz']
        widgets = {
            'tarih': forms.DateInput(attrs={'type': 'date', 'class': 'form-control form-control-sm'}),
            'tutar': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'aciklama': forms.Textarea(attrs={'rows': 3, 'class': 'form-control form-control-sm'}),
            'makbuz': forms.ClearableFileInput(attrs={'class': 'form-control form-control-sm'}),
        }
        labels = {
            'tutar': _('Tutar (₺)'),
            'tarih': _('Ödeme Tarihi'),
            'aciklama': _('Açıklama (Opsiyonel)'),
            'makbuz': _('Makbuz (Opsiyonel)'),
        }

class GiderForm(forms.ModelForm):
    class Meta:
        model = Gider
        fields = ['tur', 'tutar', 'tarih', 'aciklama', 'makbuz']
        widgets = {
            'tur': forms.TextInput(attrs={'class': 'form-control form-control-sm'}),
            'tutar': forms.NumberInput(attrs={'class': 'form-control form-control-sm'}),
            'tarih': forms.DateInput(attrs={'type': 'date', 'class': 'form-control form-control-sm'}),
            'aciklama': forms.Textarea(attrs={'rows': 3, 'class': 'form-control form-control-sm'}),
            'makbuz': forms.ClearableFileInput(attrs={'class': 'form-control form-control-sm'}),
        }
        labels = {
            'tur': _('Gider Türü'),
            'tutar': _('Tutar (₺)'),
            'tarih': _('Gider Tarihi'),
            'aciklama': _('Açıklama (Opsiyonel)'),
            'makbuz': _('Makbuz (Opsiyonel)'),
        }
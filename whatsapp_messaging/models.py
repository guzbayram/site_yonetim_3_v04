from django.db import models
from django.conf import settings

class WhatsAppContact(models.Model):
    """WhatsApp iletişim bilgileri modeli / WhatsApp contact information model"""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    whatsapp_number = models.CharField(max_length=20, verbose_name="WhatsApp Numarası")
    is_active = models.BooleanField(default=True, verbose_name="Aktif mi?")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "WhatsApp İletişim"
        verbose_name_plural = "WhatsApp İletişimleri"

    def __str__(self):
        return f"{self.user.get_full_name()} - {self.whatsapp_number}"

class WhatsAppMessage(models.Model):
    """WhatsApp mesaj kayıtları modeli / WhatsApp message records model"""
    MESSAGE_TYPES = [
        ('PAYMENT', 'Ödeme Bildirimi'),
        ('EXPENSE', 'Gider Bildirimi'),
        ('REMINDER', 'Borç Hatırlatma'),
    ]

    STATUS_CHOICES = [
        ('PENDING', 'Beklemede'),
        ('SENT', 'Gönderildi'),
        ('FAILED', 'Başarısız'),
    ]

    message_type = models.CharField(max_length=10, choices=MESSAGE_TYPES, verbose_name="Mesaj Tipi")
    recipient = models.ForeignKey(WhatsAppContact, on_delete=models.SET_NULL, null=True, blank=True)
    content = models.TextField(verbose_name="Mesaj İçeriği")
    media_url = models.URLField(null=True, blank=True, verbose_name="Medya URL")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "WhatsApp Mesajı"
        verbose_name_plural = "WhatsApp Mesajları"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_message_type_display()} - {self.created_at}"

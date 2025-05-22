from django.db.models.signals import post_save
from django.dispatch import receiver
from yonetim.models import Aidat, Gider
from .tasks import send_payment_notification, send_expense_notification

@receiver(post_save, sender=Aidat)
def send_whatsapp_payment_notification(sender, instance, created, **kwargs):
    """
    Yeni ödeme kaydedildiğinde WhatsApp bildirimi gönder
    Send WhatsApp notification when new payment is recorded
    """
    if created:  # Sadece yeni kayıtlar için / Only for new records
        send_payment_notification.delay(instance.id)

@receiver(post_save, sender=Gider)
def send_whatsapp_expense_notification(sender, instance, created, **kwargs):
    """
    Yeni gider kaydedildiğinde WhatsApp bildirimi gönder
    Send WhatsApp notification when new expense is recorded
    """
    if created:  # Sadece yeni kayıtlar için / Only for new records
        send_expense_notification.delay(instance.id) 
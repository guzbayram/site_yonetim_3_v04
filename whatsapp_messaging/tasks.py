from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from .services import WhatsAppService

@shared_task
def check_and_send_debt_reminders():
    """
    Borçlu daireleri kontrol et ve hatırlatma gönder
    Check indebted apartments and send reminders
    """
    # Import'ları fonksiyon içine taşıyoruz / Move imports inside the function
    from yonetim.models import Daire, Aidat
    
    whatsapp_service = WhatsAppService()
    today = timezone.now().date()
    
    # Son bir ayın aidat tutarını hesapla / Calculate last month's dues amount
    last_month = today - timedelta(days=30)
    monthly_dues = Aidat.objects.filter(
        tarih__year=last_month.year,
        tarih__month=last_month.month
    ).first()
    
    if not monthly_dues:
        return "Aylık aidat tutarı bulunamadı"
    
    monthly_amount = monthly_dues.tutar
    
    # Borçlu daireleri bul / Find indebted apartments
    for daire in Daire.objects.all():
        total_debt = daire.get_total_debt()  # Bu metodu Daire modelinize eklemeniz gerekiyor
        
        if total_debt >= monthly_amount:
            # Daire sakinlerine WhatsApp mesajı gönder / Send WhatsApp message to apartment residents
            for user in daire.users.all():  # Daire modelinizde users ilişkisi olmalı
                whatsapp_service.send_debt_reminder(user, total_debt)
    
    return "Borç hatırlatmaları gönderildi"

@shared_task
def send_payment_notification(payment_id):
    """
    Ödeme bildirimi gönder
    Send payment notification
    """
    # Import'u fonksiyon içine taşıyoruz / Move import inside the function
    from yonetim.models import Aidat
    whatsapp_service = WhatsAppService()
    
    try:
        payment = Aidat.objects.get(id=payment_id)
        whatsapp_service.send_payment_notification(payment)
    except Aidat.DoesNotExist:
        return f"Ödeme bulunamadı: {payment_id}"
    except Exception as e:
        return f"Ödeme bildirimi gönderme hatası: {str(e)}"

@shared_task
def send_expense_notification(expense_id):
    """
    Gider bildirimi gönder
    Send expense notification
    """
    # Import'u fonksiyon içine taşıyoruz / Move import inside the function
    from yonetim.models import Gider
    whatsapp_service = WhatsAppService()
    
    try:
        expense = Gider.objects.get(id=expense_id)
        whatsapp_service.send_expense_notification(expense)
    except Gider.DoesNotExist:
        return f"Gider bulunamadı: {expense_id}"
    except Exception as e:
        return f"Gider bildirimi gönderme hatası: {str(e)}" 
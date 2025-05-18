from twilio.rest import Client
from django.conf import settings
from .models import WhatsAppMessage
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class WhatsAppService:
    """WhatsApp mesajlaşma servisi / WhatsApp messaging service"""
    
    def __init__(self):
        self.client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        self.from_number = settings.TWILIO_WHATSAPP_NUMBER

    def send_message(self, to_number, message, media_url=None):
        """
        WhatsApp mesajı gönderme / Send WhatsApp message
        :param to_number: Alıcı numara / Recipient number
        :param message: Mesaj içeriği / Message content
        :param media_url: Medya URL'si (opsiyonel) / Media URL (optional)
        :return: bool
        """
        try:
            # WhatsApp numarasını formatla / Format WhatsApp number
            to_number = f"whatsapp:{to_number}" if not to_number.startswith('whatsapp:') else to_number

            # Mesaj gönderme / Send message
            message_params = {
                'from_': self.from_number,
                'body': message,
                'to': to_number
            }

            if media_url:
                message_params['media_url'] = [media_url]

            message = self.client.messages.create(**message_params)
            
            return True, message.sid
        except Exception as e:
            logger.error(f"WhatsApp mesaj gönderme hatası: {str(e)}")
            return False, str(e)

    def send_payment_notification(self, payment):
        """
        Ödeme bildirimi gönderme / Send payment notification
        :param payment: Ödeme nesnesi / Payment object
        :return: WhatsAppMessage
        """
        message_content = f"""
🔔 Yeni Ödeme Bildirimi

🏢 Daire: {payment.daire}
💰 Tutar: {payment.tutar} TL
📅 Tarih: {payment.tarih}
👤 Ödeyen: {payment.odeme_yapan}

Detaylar için sisteme giriş yapabilirsiniz.
        """

        # Grup mesajı gönder / Send group message
        success, message_sid = self.send_message(
            settings.SITE_WHATSAPP_GROUP,
            message_content,
            payment.dekont.url if hasattr(payment, 'dekont') and payment.dekont else None
        )

        # Mesaj kaydını oluştur / Create message record
        return WhatsAppMessage.objects.create(
            message_type='PAYMENT',
            content=message_content,
            media_url=payment.dekont.url if hasattr(payment, 'dekont') and payment.dekont else None,
            status='SENT' if success else 'FAILED',
            sent_at=datetime.now() if success else None
        )

    def send_expense_notification(self, expense):
        """
        Gider bildirimi gönderme / Send expense notification
        :param expense: Gider nesnesi / Expense object
        :return: WhatsAppMessage
        """
        message_content = f"""
🔔 Yeni Gider Bildirimi

📝 Gider Türü: {expense.gider_turu}
💰 Tutar: {expense.tutar} TL
📅 Tarih: {expense.tarih}
👤 Kaydeden: {expense.kaydeden}

Detaylar için sisteme giriş yapabilirsiniz.
        """

        # Grup mesajı gönder / Send group message
        success, message_sid = self.send_message(
            settings.SITE_WHATSAPP_GROUP,
            message_content,
            expense.fatura.url if hasattr(expense, 'fatura') and expense.fatura else None
        )

        # Mesaj kaydını oluştur / Create message record
        return WhatsAppMessage.objects.create(
            message_type='EXPENSE',
            content=message_content,
            media_url=expense.fatura.url if hasattr(expense, 'fatura') and expense.fatura else None,
            status='SENT' if success else 'FAILED',
            sent_at=datetime.now() if success else None
        )

    def send_debt_reminder(self, user, debt_amount):
        """
        Borç hatırlatma mesajı gönderme / Send debt reminder message
        :param user: Kullanıcı nesnesi / User object
        :param debt_amount: Borç tutarı / Debt amount
        :return: WhatsAppMessage
        """
        try:
            contact = user.whatsappcontact
            if not contact or not contact.is_active:
                return None

            message_content = f"""
🔔 Aidat Borç Hatırlatması

Sayın {user.get_full_name()},

Aidat borcunuz {debt_amount} TL'ye ulaşmıştır. 
Lütfen en kısa sürede ödemenizi yapınız.

Detaylı bilgi için sisteme giriş yapabilirsiniz.

Saygılarımızla,
Site Yönetimi
            """

            # Bireysel mesaj gönder / Send individual message
            success, message_sid = self.send_message(
                contact.whatsapp_number,
                message_content
            )

            # Mesaj kaydını oluştur / Create message record
            return WhatsAppMessage.objects.create(
                message_type='REMINDER',
                recipient=contact,
                content=message_content,
                status='SENT' if success else 'FAILED',
                sent_at=datetime.now() if success else None
            )
        except Exception as e:
            logger.error(f"Borç hatırlatma mesajı gönderme hatası: {str(e)}")
            return None 
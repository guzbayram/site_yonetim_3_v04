from django.apps import AppConfig


class WhatsAppMessagingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "whatsapp_messaging"
    verbose_name = "WhatsApp Mesajlaşma"

    def ready(self):
        """
        Uygulama başlatıldığında sinyalleri yükle
        Load signals when the app is ready
        """
        import whatsapp_messaging.signals  # noqa

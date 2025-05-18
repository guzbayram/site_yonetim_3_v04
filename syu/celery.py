import os
from celery import Celery
from celery.schedules import crontab

# Django ayarlarını Celery'ye bildir / Set Django settings for Celery
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syu.settings')

app = Celery('syu')

# Django ayarlarından Celery yapılandırmasını yükle / Load Celery config from Django settings
app.config_from_object('django.conf:settings', namespace='CELERY')

# Görevleri otomatik yükle / Auto-discover tasks
app.autodiscover_tasks()

# Periyodik görevleri tanımla / Define periodic tasks
app.conf.beat_schedule = {
    'check-debt-reminders': {
        'task': 'whatsapp_messaging.tasks.check_and_send_debt_reminders',
        # Her ayın 1'i saat 10:00'da çalıştır / Run on 1st day of each month at 10:00
        'schedule': crontab(hour=10, minute=0, day_of_month=1),
    },
}

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}') 
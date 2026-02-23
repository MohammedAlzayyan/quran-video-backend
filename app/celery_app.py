import os
import ssl
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

# الرابط الأصلي من البيئة
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0").strip()

# تعيين إعدادات SSL الصارمة
ssl_conf = None
if redis_url.startswith("rediss://"):
    ssl_conf = {
        'ssl_cert_reqs': ssl.CERT_NONE  # تجاوز الشهادات للاتصال بـ Upstash
    }

celery_app = Celery(
    "quran_video_tasks",
    broker=redis_url,
    backend=redis_url,
    include=["app.tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=1800,
    
    # الإعدادات الحاسمة لتجاوز مشكلة Retry limit exceeded
    broker_use_ssl=ssl_conf,
    redis_backend_use_ssl=ssl_conf,
    broker_connection_retry_on_startup=True,
    
    # خيارات النقل لضمان استقرار الاتصال بـ Upstash
    broker_transport_options={
        'ssl': ssl_conf,
        'retry_on_timeout': True,
        'socket_timeout': 30,
        'socket_connect_timeout': 30,
    } if ssl_conf else {},
    
    result_backend_transport_options={
        'ssl': ssl_conf,
        'retry_on_timeout': True,
    } if ssl_conf else {},
)

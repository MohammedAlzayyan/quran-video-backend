import os
import ssl
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

# الطريقة الأضمن لـ Upstash هي إضافة البارامترات مباشرة للرابط
original_redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0").strip()
if original_redis_url.endswith('/'):
    original_redis_url = original_redis_url[:-1]

# إذا كان الرابط آمن، نضيف تعليمة تجاهل الشهادة داخل الرابط نفسه لضمان وصولها للباك اند
if original_redis_url.startswith("rediss://"):
    if "?" in original_redis_url:
        redis_url = f"{original_redis_url}&ssl_cert_reqs=none"
    else:
        redis_url = f"{original_redis_url}?ssl_cert_reqs=none"
else:
    redis_url = original_redis_url

ssl_conf = None
if redis_url.startswith("rediss://"):
    ssl_conf = {'ssl_cert_reqs': ssl.CERT_NONE}

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
    # إعدادات إضافية للتأكيد
    broker_use_ssl=ssl_conf,
    redis_backend_use_ssl=ssl_conf,
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        'ssl': ssl_conf,
        'retry_on_timeout': True,
    } if ssl_conf else {},
)

import os
import ssl
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

# تنظيف الرابط بشكل عدواني لمنع الـ // الزائدة
raw_url = os.getenv("REDIS_URL", "redis://localhost:6379/0").strip()
# إزالة السلاشات من النهاية
while raw_url.endswith('/'):
    raw_url = raw_url[:-1]

# بناء الرابط مع بارامترات SSL لضمان وصولها لجميع المحركات
if raw_url.startswith("rediss://"):
    if "?" in raw_url:
        redis_url = f"{raw_url}&ssl_cert_reqs=none"
    else:
        redis_url = f"{raw_url}?ssl_cert_reqs=none"
else:
    redis_url = raw_url

# إعدادات SSL الصريحة لـ Upstash
ssl_conf = None
if redis_url.startswith("rediss://"):
    ssl_conf = {
        'ssl_cert_reqs': ssl.CERT_NONE
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
    # إعدادات SSL الحاسمة
    broker_use_ssl=ssl_conf,
    redis_backend_use_ssl=ssl_conf,
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        'ssl': ssl_conf,
        'retry_on_timeout': True,
    } if ssl_conf else {},
    result_backend_transport_options={
        'ssl': ssl_conf,
        'retry_on_timeout': True,
    } if ssl_conf else {},
)

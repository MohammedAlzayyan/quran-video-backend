import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# إعدادات الـ SSL مهمة جداً للاتصال بقواعد بيانات Redis السحابية (Koyeb/Railway/etc)
# إذا كان الرابط يبدأ بـ rediss (آمن)، يجب تفعيل خيارات التجاوز لنجاح الاتصال
ssl_options = None
if redis_url.startswith("rediss://"):
    import ssl
    ssl_options = {
        "ssl_cert_reqs": ssl.CERT_NONE  # تجاوز فحص الشهادة للاتصال السريع
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
    task_time_limit=1800, # 30 minutes
    # هذه الإعدادات هي التي تحل مشكلة الـ Retry limit exceeded
    broker_use_ssl=True if ssl_options else False,
    redis_backend_use_ssl=ssl_options if ssl_options else False,
    broker_transport_options={
        "retry_on_timeout": True,
        "ssl": ssl_options
    } if ssl_options else {},
    result_backend_transport_options={
        "ssl": ssl_options
    } if ssl_options else {},
)

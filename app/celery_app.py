import os
import ssl
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

# الرابط الأصلي
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0").strip()

# إعداد SSL قوي لـ Upstash
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
    
    # إعدادات لتقليل استهلاك الذاكرة (منع الـ OOM)
    worker_max_tasks_per_child=10,  # إعادة تشغيل العامل بعد 10 مهام لتنظيف الرام
    worker_concurrency=1,           # مهم جداً للـ Free Instances
    
    # حل مشكلة الـ Retry limit exceeded
    broker_use_ssl=ssl_conf,
    redis_backend_use_ssl=ssl_conf,
    broker_connection_retry_on_startup=True,
    broker_heartbeat=10,
    broker_transport_options={
        'ssl': ssl_conf,
        'retry_on_timeout': True,
        'visibility_timeout': 3600,
    } if ssl_conf else {},
    
    # ضبط الباك اند ليكون أكثر صبراً
    redis_backend_health_check_interval=30,
    result_backend_transport_options={
        'ssl': ssl_conf,
        'retry_on_timeout': True,
    } if ssl_conf else {},
)

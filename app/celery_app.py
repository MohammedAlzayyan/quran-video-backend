import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

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
    beat_schedule={
        "cleanup-expired-videos-daily": {
            "task": "app.tasks.cleanup_old_videos",
            "schedule": 86400.0, # Every 24 hours
        },
    },
)

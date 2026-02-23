import os
import time
import shutil
import tempfile
import asyncio
import threading
from .celery_app import celery_app
from .database import SessionLocal
from . import models
from .audio_processor import process_audio
from .ai_video_service import fetch_nature_clips
from .video_generator import generate_final_video
from .utils import fetch_arabic_text_list
from .storage_service import upload_video_to_supabase

@celery_app.task(bind=True)
def generate_video_task(self, job_id, video_request_dict):
    db = SessionLocal()
    temp_files_to_clean = []
    
    try:
        # 1. Update job status to processing
        job = db.query(models.VideoJob).filter(models.VideoJob.id == job_id).first()
        if not job:
            print(f"❌ Job {job_id} not found in database")
            return
        
        job.status = "processing"
        job.progress_message = "⏳ جاري بدء العملية..."
        db.commit()
        
        def update_progress(msg):
            try:
                # Need to refresh/re-fetch to avoid session issues in sometimes long tasks
                curr_job = db.query(models.VideoJob).filter(models.VideoJob.id == job_id).first()
                if curr_job:
                    curr_job.progress_message = msg
                    db.commit()
            except: pass

        print(f"🚀 Starting background generation for Job {job_id}")
        
        # Helper to run async functions in a sync task
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
        def run_async(coro):
            return loop.run_until_complete(coro)

        # Extraction of parameters
        surah = video_request_dict['surah']
        start_ayah = video_request_dict['startAyah']
        end_ayah = video_request_dict['endAyah']
        reciter = video_request_dict['reciter']
        nature_scenes = video_request_dict['natureScenes']
        font_family = video_request_dict.get('fontFamily', 'quran-uthmani')
        show_english = video_request_dict.get('showEnglish', False)
        
        # 1. Fetch Audio and Timings
        update_progress("🎵 جاري تجهيز الملف الصوتي والتوقيت...")
        time.sleep(1) # ضمان رؤية الرسالة في الواجهة
        print(f"Job {job_id}: Processing audio...")
        audio_path, duration, ayah_timings = run_async(process_audio(reciter, surah, start_ayah, end_ayah))
        temp_files_to_clean.append(audio_path)
        
        # 2. Fetch Arabic text list
        update_progress("📖 جاري جلب آيات القرآن والترجمة...")
        time.sleep(1)
        print(f"Job {job_id}: Fetching Quran text...")
        ayah_texts = run_async(fetch_arabic_text_list(
            surah, start_ayah, end_ayah, 
            edition=font_family,
            fetch_translation=show_english
        ))
        
        # 3. Synchronize Text with Timings
        update_progress("🔄 جاري مزامنة النصوص مع الصوت...")
        time.sleep(0.5)
        synced_data = []
        for timing in ayah_timings:
            text_data = next((t for t in ayah_texts if t['ayah_num'] == timing['ayah_num']), None)
            if text_data:
                synced_data.append({
                    'words': text_data['words'],
                    'translation': text_data.get('translation', ''),
                    'start': timing['start'],
                    'end': timing['end']
                })
        
        # 4. Get Nature Video Clips
        update_progress("🎬 جاري البحث عن مشاهد الفيديو وتحميلها...")
        time.sleep(1)
        print(f"Job {job_id}: Fetching nature clips...")
        video_clips = run_async(fetch_nature_clips(nature_scenes, duration))
        
        # 5. Generate Final Video
        update_progress("🖥️ جاري رندرة الفيديو (قد تستغرق دقيقتين)...")
        print(f"Job {job_id}: Rendering video...")
        fd, temp_video_path = tempfile.mkstemp(suffix=".mp4", prefix=f"quran_job_{job_id}_")
        os.close(fd)
        temp_files_to_clean.append(temp_video_path)
        
        abort_event = threading.Event() # Background tasks don't usually abort via HTTP disconnect
        
        generate_final_video(
            audio_path=audio_path, 
            video_scenes=video_clips, 
            output_path=temp_video_path,
            duration=duration,
            synced_data=synced_data,
            font_size=video_request_dict.get('fontSize', 50),
            show_highlight=video_request_dict.get('showHighlight', True),
            highlight_color=video_request_dict.get('highlightColor', "#282828"),
            highlight_opacity=video_request_dict.get('highlightOpacity', 0.8),
            position=video_request_dict.get('position', "center"),
            show_arabic=video_request_dict.get('showArabic', True),
            show_english=video_request_dict.get('showEnglish', False),
            font_family=font_family,
            text_color=video_request_dict.get('textColor', "#ffffff"),
            abort_event=abort_event,
            display_mode=video_request_dict.get('displayMode', "ayah"),
            top_text=video_request_dict.get('topText', ""),
            show_video_overlay=video_request_dict.get('showVideoOverlay', False),
            background_type=video_request_dict.get('backgroundType', "video"),
            reciter_name=video_request_dict.get('reciterName', ""),
            surah_name=video_request_dict.get('surahName', "")
        )
        
        # 6. Move to persistent storage
        user_storage_dir = os.path.join("storage", "videos", f"user_{job.user_id}")
        os.makedirs(user_storage_dir, exist_ok=True)
        
        timestamp = int(time.time())
        persistent_filename = f"quran_{surah}_{timestamp}.mp4"
        persistent_path = os.path.join(user_storage_dir, persistent_filename)
        
        shutil.move(temp_video_path, persistent_path)
        relative_video_path = os.path.join("videos", f"user_{job.user_id}", persistent_filename).replace("\\", "/")
        
        # 7. Update Database to COMPLETED with local path
        # This gives immediate feedback to the user and local video link
        job.status = "completed"
        job.progress_message = "✅ تم الانتهاء بنجاح!"
        job.video_path = relative_video_path
        db.commit()
        
        print(f"✅ Job {job_id} localized successfully! Local path: {relative_video_path}")
        
        # 8. Start Background Upload to Cloud (Optional but good for scalability)
        upload_to_supabase_task.delay(job_id, persistent_path, f"user_{job.user_id}/{persistent_filename}")
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            job = db.query(models.VideoJob).filter(models.VideoJob.id == job_id).first()
            if job:
                job.status = "failed"
                db.commit()
        except: pass
        print(f"❌ Job {job_id} failed: {str(e)}")
    finally:
        # Cleanup
        for f in temp_files_to_clean:
            try:
                # We DON'T remove persistent_path here because it's the final video
                if os.path.exists(f) and f != persistent_path: 
                    os.remove(f)
            except: pass
        db.close()

@celery_app.task
def upload_to_supabase_task(job_id, local_path, cloud_filename):
    """
    Background task to upload a video to Supabase and update the DB record.
    """
    db = SessionLocal()
    try:
        print(f"☁️ Starting background cloud upload for Job {job_id}...")
        cloud_url = upload_video_to_supabase(local_path, cloud_filename)
        
        if cloud_url:
            job = db.query(models.VideoJob).filter(models.VideoJob.id == job_id).first()
            if job:
                job.video_path = cloud_url
                db.commit()
                print(f"✅ Job {job_id} updated with cloud URL: {cloud_url}")
            
            # � تأجيل الحذف: جدولة عملية حذف الملف المحلي بعد 30 دقيقة
            # نستخدم apply_async مع countdown لضمان عدم شغل الـ Worker طول هذه المدة
            delete_local_video_task.apply_async(args=[local_path], countdown=1800)
            print(f"🕒 Local file deletion scheduled in 30 minutes for: {local_path}")
        else:
            print(f"⚠️ Background cloud upload failed for Job {job_id}")
            
    except Exception as e:
        print(f"❌ Background upload error for Job {job_id}: {e}")
    finally:
        db.close()

@celery_app.task
def delete_local_video_task(file_path):
    """
    Schedules the deletion of a local video file to save space after usage.
    """
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"🗑️ Scheduled deletion complete: {file_path}")
        else:
            print(f"ℹ️ File already removed or not found: {file_path}")
    except Exception as e:
        print(f"⚠️ Error during scheduled deletion of {file_path}: {e}")

@celery_app.task
def cleanup_old_videos():
    """
    Task to delete video files older than 14 days to save storage space.
    Runs daily via Celery Beat.
    """
    from datetime import datetime, timedelta
    db = SessionLocal()
    try:
        # 1. Define the expiration threshold
        expiration_date = datetime.utcnow() - timedelta(days=14)
        
        # 2. Find old jobs that still have a video path
        old_jobs = db.query(models.VideoJob).filter(
            models.VideoJob.created_at < expiration_date,
            models.VideoJob.video_path != None
        ).all()
        
        count = 0
        for job in old_jobs:
            # Construct the absolute path
            # video_path is relative to 'storage' directory
            file_path = os.path.join("storage", job.video_path)
            
            # 3. Delete the physical file
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    print(f"🗑️ Deleted expired video: {file_path}")
                except Exception as e:
                    print(f"⚠️ Failed to delete file {file_path}: {e}")
            
            # 4. Update the database record
            # We NULL the path so it's no longer accessible but history remains
            job.video_path = None
            count += 1
            
        db.commit()
        print(f"✅ Cleanup complete: Removed {count} expired videos.")
        
    except Exception as e:
        print(f"❌ Error during cleanup task: {e}")
    finally:
        db.close()

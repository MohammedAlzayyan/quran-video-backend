from fastapi import APIRouter, HTTPException, Request, Depends
from .dependencies import get_current_user, get_db
from . import models, schemas
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List
from app.video_generator import generate_final_video, RequestAborted
from app.audio_processor import process_audio, RECITER_MAP
from app.ai_video_service import fetch_nature_clips
import os
import httpx
import asyncio
import threading
import time
import shutil

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

router = APIRouter()

@router.get("/surahs")
async def get_surahs():
    async with httpx.AsyncClient() as client:
        response = await client.get("https://api.alquran.cloud/v1/surah")
        if response.status_code == 200:
            return response.json()['data']
    return []

@router.get("/reciters")
async def get_reciters():
    return list(RECITER_MAP.keys())

@router.get("/fonts")
async def get_fonts():
    # نعتمد حصرياً على "الإصدارات" (Editions) المتاحة في AlQuran.cloud
    # لضمان أعلى دقة للنص القرآني
    return [
        {"name": "خط المصحف (نسخ طه)", "value": "Mushaf-Naskh.ttf"},
        {"name": "خط حفص (Uthmanic HAFS)", "value": "quran-uthmani"},
        {"name": "نص التجويد الملون", "value": "quran-tajweed"},
        {"name": "النص البسيط (Simple)", "value": "quran-simple"},
        {"name": "النص البسيط (Enhanced)", "value": "quran-simple-enhanced"},
        {"name": "النص البسيط (Clean)", "value": "quran-simple-clean"},
        {"name": "نص كلمة بكلمة (Word-by-Word)", "value": "quran-wordbyword"}
    ]

def debug_list_fonts():
    print("📋 Diagnostic: Listing available system fonts...")
    font_paths = ["/usr/share/fonts", "/usr/local/share/fonts", "~/.local/share/fonts"]
    for p in font_paths:
        p = os.path.expanduser(p)
        if os.path.exists(p):
            for root, dirs, files in os.walk(p):
                for f in files:
                    if f.lower().endswith(('.ttf', '.otf')):
                        print(f"  - {f} (in {root})")
        else:
            print(f"  - Directory not found: {p}")

debug_list_fonts()

from .utils import fetch_arabic_text_list
from .tasks import generate_video_task

class VideoRequest(BaseModel):
    reciter: str
    surah: int
    startAyah: int
    endAyah: int
    natureScenes: List[str]
    # Existing styling options
    fontSize: int = 50
    showHighlight: bool = True
    highlightColor: str = "#282828"
    highlightOpacity: float = 0.8
    position: str = "center"
    textColor: str = "#ffffff"
    # New options
    showArabic: bool = True
    showEnglish: bool = False
    fontFamily: str = "Noto Sans Arabic"
    displayMode: str = "ayah" # "ayah" or "chunked"
    topText: str = ""
    showVideoOverlay: bool = False
    backgroundType: str = "video" # "video" or "image"
    reciterName: str = ""
    surahName: str = ""

class PreviewRequest(BaseModel):
    reciter: str = "مشاري العفاسي"
    natureScenes: List[str] = ["مساجد"]
    fontSize: int = 50
    showHighlight: bool = True
    highlightColor: str = "#282828"
    highlightOpacity: float = 0.8
    position: str = "center"
    textColor: str = "#ffffff"
    showArabic: bool = True
    showEnglish: bool = False
    fontFamily: str = "Noto Naskh Arabic"
    displayMode: str = "ayah"
    # Sample text to preview
    sampleArabic: str = "بِسْمِ ٱللَّهِ ٱلرَّحْمَـٰنِ ٱلرَّحِيمِ"
    sampleEnglish: str = "In the name of Allah, the Entirely Merciful, the Especially Merciful."
    topText: str = ""
    showVideoOverlay: bool = False
    backgroundType: str = "video"
    reciterName: str = "مشاري العفاسي"
    surahName: str = "الفاتحة"

from fastapi.responses import FileResponse
from starlette.background import BackgroundTasks
import tempfile

@router.post("/generate-video")
async def generate_video(video_request: VideoRequest, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """
    Starts a background task to generate the video and returns the job ID immediately.
    """
    try:
        # Create a new job record with 'processing' status
        new_job = models.VideoJob(
            user_id=current_user.id,
            surah_name=video_request.surahName or f"Surah {video_request.surah}",
            reciter_name=video_request.reciterName or video_request.reciter,
            ayah_range=f"{video_request.startAyah}-{video_request.endAyah}",
            status="processing" # Mark as processing from the start
        )
        db.add(new_job)
        db.commit()
        db.refresh(new_job)
        
        # Dispatch the Celery task
        # We pass the dictionary of the request to avoid Pydantic serialization issues in Celery
        generate_video_task.delay(new_job.id, video_request.dict())
        
        return {
            "message": "Video generation started in background",
            "job_id": new_job.id,
            "status": "processing"
        }

    except Exception as e:
        print(f"❌ Error starting video generation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


from app.video_generator import generate_preview_video, generate_preview_image

@router.post("/generate-preview-image")
async def generate_preview_img(request: PreviewRequest, background_tasks: BackgroundTasks, current_user: models.User = Depends(get_current_user)):
    try:
        preview_path = await asyncio.to_thread(
            generate_preview_image,
            sample_arabic=request.sampleArabic,
            sample_english=request.sampleEnglish,
            nature_scenes=request.natureScenes,
            font_size=request.fontSize,
            show_highlight=request.showHighlight,
            highlight_color=request.highlightColor,
            highlight_opacity=request.highlightOpacity,
            position=request.position,
            show_arabic=request.showArabic,
            show_english=request.showEnglish,
            font_family=request.fontFamily,
            text_color=request.textColor,
            display_mode=request.displayMode,
            top_text=request.topText,
            show_video_overlay=request.showVideoOverlay,
            background_type=request.backgroundType,
            reciter_name=request.reciterName,
            surah_name=request.surahName
        )
        
        if not preview_path or not os.path.exists(preview_path):
             raise HTTPException(status_code=500, detail="Failed to generate preview image")

        def cleanup_file():
            try:
                if os.path.exists(preview_path): os.remove(preview_path)
            except: pass

        background_tasks.add_task(cleanup_file)
        
        return FileResponse(preview_path, media_type="image/jpeg")

    except Exception as e:
        print(f"❌ Preview image error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/generate-preview")
async def generate_preview(request: PreviewRequest, background_tasks: BackgroundTasks, current_user: models.User = Depends(get_current_user)):
    try:
        # 1. جلب رابط صوت البسملة للشيخ المختار
        # البسملة دائماً هي الآية 1 في سورة 1 (الفاتحة) في EveryAyah
        audio_url = None
        reciter_id = RECITER_MAP.get(request.reciter, "Alafasy_128kbps")
        audio_url = f"https://everyayah.com/data/{reciter_id}/001001.mp3"

        print(f"🎬 Preview Video Generation Request: {request.dict()} | Audio: {audio_url}")
        try:
            async with httpx.AsyncClient() as client:
                # استعادة البسملة للمعاينة ليتطابق النص مع الصوت
                res = await client.get("https://api.alquran.cloud/v1/ayah/1:1/quran-uthmani")
                if res.status_code == 200:
                    sample_arabic = res.json()['data']['text']
        except Exception as e:
            sample_arabic = "بِسْمِ ٱللَّهِ ٱلرَّحْمَـٰنِ ٱلرَّحِيمِ"

        # 2. توليد فيديو المعاينة
        preview_path = await asyncio.to_thread(
            generate_preview_video,
            sample_arabic=sample_arabic,
            sample_english=request.sampleEnglish,
            nature_scenes=request.natureScenes,
            audio_url=audio_url,
            font_size=request.fontSize,
            show_highlight=request.showHighlight,
            highlight_color=request.highlightColor,
            highlight_opacity=request.highlightOpacity,
            position=request.position,
            show_arabic=request.showArabic,
            show_english=request.showEnglish,
            font_family=request.fontFamily,
            text_color=request.textColor,
            display_mode=request.displayMode,
            top_text=request.topText,
            show_video_overlay=request.showVideoOverlay,
            background_type=request.backgroundType,
            reciter_name=request.reciterName,
            surah_name=request.surahName
        )
        
        if not preview_path or not os.path.exists(preview_path):
             print("❌ Preview path calculation failed or file not found - attempting fallback generation")
             # محاولة توليد معاينة مبسطة جداً في حالة الفشل
             try:
                 preview_path = await asyncio.to_thread(
                     generate_preview_video,
                     sample_arabic=sample_arabic,
                     sample_english=request.sampleEnglish,
                     nature_scenes=request.natureScenes,
                     audio_url=None, # بدون صوت لتقليل الضغط
                     font_size=request.fontSize,
                     show_highlight=request.showHighlight,
                     highlight_color=request.highlightColor,
                     position=request.position,
                     show_arabic=request.showArabic,
                     show_english=request.showEnglish,
                     font_family=request.fontFamily,
                     display_mode=request.displayMode,
                     top_text=request.topText,
                     show_video_overlay=request.showVideoOverlay,
                     background_type=request.backgroundType,
                     text_color=request.textColor,
                     reciter_name=request.reciterName,
                     surah_name=request.surahName
                 )
             except: pass
             
             if not preview_path or not os.path.exists(preview_path):
                 raise HTTPException(status_code=500, detail="Failed to generate preview video")

        # Signup cleanup task
        def cleanup_file():
            try:
                if os.path.exists(preview_path):
                    os.remove(preview_path)
                    print(f"🗑️ Deleted preview file: {preview_path}")
            except Exception as e:
                print(f"Error cleaning up preview: {e}")

        background_tasks.add_task(cleanup_file)
        
        return FileResponse(
            preview_path, 
            media_type="video/mp4", 
            filename="preview.mp4",
            headers={"Cache-Control": "no-store"}
        )

    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        print(f"❌ Preview generation deep error:\n{error_detail}")
        raise HTTPException(status_code=500, detail=f"Preview Error: {str(e)}")

@router.get("/history")
async def get_video_history(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """
    Get the history of generated videos for the current user.
    """
    jobs = db.query(models.VideoJob).filter(models.VideoJob.user_id == current_user.id).order_by(models.VideoJob.created_at.desc()).all()
    
    # Construct full URLs for the videos
    for job in jobs:
        if job.video_path:
            if job.video_path.startswith("http"):
                job.download_url = job.video_path
            else:
                job.download_url = f"{BACKEND_URL}/static/{job.video_path}"
        else:
            job.download_url = None
            
    return jobs

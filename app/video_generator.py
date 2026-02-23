import os
import requests
import subprocess
import numpy as np
from PIL import Image, ImageDraw, ImageFont
# Fix for Pillow 10+ compatibility with MoviePy 1.0.3
if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = Image.Resampling.LANCZOS
import arabic_reshaper
from bidi.algorithm import get_display
from pathlib import Path
try:
    from moviepy.editor import ColorClip, CompositeVideoClip, AudioFileClip, VideoFileClip, concatenate_videoclips, ImageClip, VideoClip
except ImportError:
    # Support for MoviePy v2.0+
    from moviepy import ColorClip, CompositeVideoClip, AudioFileClip, VideoFileClip, concatenate_videoclips, ImageClip, VideoClip
from app.ai_video_service import SCENE_COLORS, SCENE_KEYWORDS, SCENE_EXCLUDE_KEYWORDS, GLOBAL_EXCLUDE_KEYWORDS
from proglog import ProgressBarLogger
import threading
import traceback
import tempfile
import requests
import shutil
import time

# --- MoviePy Compatibility Layer ---
def set_duration(clip, duration):
    if hasattr(clip, 'with_duration'): return clip.with_duration(duration)
    if hasattr(clip, 'set_duration'): return clip.set_duration(duration)
    return clip

def set_start(clip, start_time):
    if hasattr(clip, 'with_start'): return clip.with_start(start_time)
    if hasattr(clip, 'set_start'): return clip.set_start(start_time)
    return clip

def set_audio(clip, audio):
    if hasattr(clip, 'with_audio'): return clip.with_audio(audio)
    if hasattr(clip, 'set_audio'): return clip.set_audio(audio)
    return clip

def set_mask(clip, mask):
    if hasattr(clip, 'with_mask'): 
        if mask: clip.mask = mask
        return clip
    clip.mask = mask
    return clip

def create_video_clip(frame_func, duration, is_mask=False):
    """
    Robust VideoClip creation supporting MoviePy v1 (make_frame) and v2 (frame_function).
    """
    try:
        if is_mask:
            return VideoClip(frame_function=frame_func, duration=duration, is_mask=True)
        return VideoClip(frame_function=frame_func, duration=duration)
    except TypeError:
        try:
            if is_mask:
                return VideoClip(make_frame=frame_func, duration=duration, is_mask=True)
            return VideoClip(make_frame=frame_func, duration=duration)
        except TypeError:
            # Fallback to positional
            if is_mask:
                 clip = VideoClip(frame_func, duration=duration)
                 clip.is_mask = True
                 return clip
            return VideoClip(frame_func, duration=duration)

def subclip(clip, start, end):
    if hasattr(clip, 'subclipped'): return clip.subclipped(start, end)
    if hasattr(clip, 'subclip'): return clip.subclip(start, end)
    return clip

def resize_clip(clip, height=None, width=None):
    if hasattr(clip, 'resized'): return clip.resized(height=height, width=width)
    if hasattr(clip, 'resize'): return clip.resize(height=height, width=width)
    return clip

def apply_crossfade(clip, duration):
    if hasattr(clip, 'crossfadein'): return clip.crossfadein(duration)
    try:
        from moviepy.video.fx.all import crossfadein
        return clip.fx(crossfadein, duration)
    except:
        return clip

def apply_zoom(clip, duration):
    """
    Applies a smooth Ken Burns zoom effect (15% scale over duration).
    """
    if duration <= 0: return clip
    # Ensure the clip is treated as a video clip for time-based effects
    try:
        return clip.resize(lambda t: 1.0 + 0.15 * (t / duration))
    except:
        try:
            from moviepy.video.fx.all import resize
            return clip.fx(resize, lambda t: 1.0 + 0.15 * (t / duration))
        except:
            return clip
# -----------------------------------
# --- Helpers ---
def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 3:
        hex_color = ''.join([c*2 for c in hex_color])
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

class RequestAborted(Exception):
    pass

class AbortLogger(ProgressBarLogger):
    def __init__(self, abort_event=None, **kwargs):
        super().__init__(**kwargs)
        self.abort_event = abort_event

    def callback(self, **kwargs):
        if self.abort_event and self.abort_event.is_set():
            raise RequestAborted("Generation aborted by user.")
        super().callback(**kwargs)


# Pexels API Key
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "").strip()
print(f"🔑 Pexels API Key configured: {'Yes' if PEXELS_API_KEY else 'No'}")
if PEXELS_API_KEY:
    print(f"🔑 Key prefix: {PEXELS_API_KEY[:5]}...")


def is_image_appropriate(image_data: dict, scene_name: str) -> bool:
    """
    فحص ما إذا كانت الصورة مناسبة بناءً على الوصف والكلمات المستبعدة.
    """
    # 1. قائمة الكلمات المستبعدة (العامة + الخاصة بالمشهد)
    exclude_keywords = GLOBAL_EXCLUDE_KEYWORDS + SCENE_EXCLUDE_KEYWORDS.get(scene_name, [])
    
    image_url = image_data.get('url', '').lower()
    image_alt = image_data.get('alt', '').lower()
    image_user = image_data.get('photographer', '').lower()
    
    # نجمع كل البيانات للفحص
    text_to_check = f"{image_url} {image_alt} {image_user}"
    
    for keyword in exclude_keywords:
        if keyword.lower() in text_to_check:
            # فلترة خاصة: الحفاظ على الحرمين بالرغم من وجود مصلين (كلمة people)
            if scene_name in ["المدينة المنورة", "مساجد"] and keyword.lower() in ["people", "person", "man", "crowd"]:
                continue
            
            print(f"   ⛔ رفض صورة ID {image_data.get('id', 'unknown')}: تحتوي على المحتوى المحظور '{keyword}'")
            return False
    return True

def download_nature_images(scene_names: list, target_duration: float, output_dir: str = None, abort_event: threading.Event = None) -> list:
    """
    تحميل صور عشوائية من Pexels بناءً على المشاهد المختارة.
    """
    import tempfile
    if output_dir is None:
        output_dir = os.path.join(tempfile.gettempdir(), "quran_video_images")
    
    if not PEXELS_API_KEY:
        print("❌ Warning: PEXELS_API_KEY not set for images.")
        return []

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    import random
    import math

    # سنحاول جلب صورة واحدة لكل مشهد مختار، أو عدد كافٍ من الصور ليشغل الفيديو
    # لنفترض أن كل صورة ستظهر لمدة 5-10 ثوانٍ إذا كانت هناك صور متعددة
    all_image_options = []
    seen_ids = set()

    for scene in scene_names:
        if abort_event and abort_event.is_set():
            raise RequestAborted("Aborted during image search.")
        keyword = SCENE_KEYWORDS.get(scene, scene)
        print(f"🖼️ Searching Pexels for images: {keyword}...")
        
        try:
            headers = {"Authorization": PEXELS_API_KEY}
            params = {
                "query": keyword, 
                "orientation": "portrait",
                "per_page": 15,
                "page": random.randint(1, 5) # رندوم لضمان التنوع
            }
            resp = requests.get("https://api.pexels.com/v1/search", headers=headers, params=params, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                photos = data.get("photos", [])
                for p in photos:
                    if p.get('id') in seen_ids: continue
                    if not is_image_appropriate(p, scene): continue
                    
                    # نفضل جودة 'large' أو 'original'
                    best_url = p.get('src', {}).get('large2x') or p.get('src', {}).get('large') or p.get('src', {}).get('original')
                    if best_url:
                        seen_ids.add(p.get('id'))
                        all_image_options.append(best_url)
            else:
                print(f"   ❌ Pexels Image API Error: {resp.status_code}")
        except Exception as e:
            print(f"   ❌ Error searching for images ({scene}): {e}")

    if not all_image_options:
        return []

    # اختيار عدد صور مناسب (مثلاً صورة لكل 7 ثوانٍ، أو 1 إذا اختار اليوزر مشهد واحد)
    max_images = max(len(scene_names), math.ceil(target_duration / 7))
    selected_urls = random.sample(all_image_options, min(len(all_image_options), max_images))
    
    downloaded_paths = []
    for i, url in enumerate(selected_urls):
        if abort_event and abort_event.is_set():
            raise RequestAborted("Aborted during image download.")
            
        try:
            filename = f"image_{i}_{random.randint(1000, 9999)}.jpg"
            save_path = os.path.join(output_dir, filename)
            
            img_resp = requests.get(url, timeout=20)
            if img_resp.status_code == 200:
                with open(save_path, 'wb') as f:
                    f.write(img_resp.content)
                downloaded_paths.append(save_path)
        except Exception as e:
            print(f"   ❌ Error downloading image {url}: {e}")

    return downloaded_paths

def is_video_appropriate(video_data: dict, scene_name: str) -> bool:
    """
    فحص ذكي لما إذا كان الفيديو مناسباً للمحتوى القرآني (Smart Content Filter).
    """
    # 1. تجميع قائمة الكلمات المحظورة (العامة + الخاصة بالمشهد)
    exclude_keywords = GLOBAL_EXCLUDE_KEYWORDS + SCENE_EXCLUDE_KEYWORDS.get(scene_name, [])
    
    # 2. استخراج البيانات للفحص
    video_url = video_data.get('url', '').lower()
    # Pexels غالباً يضع وسوماً في الرابط نفسه
    video_user = video_data.get('user', {}).get('name', '').lower()
    
    video_tags = []
    if 'tags' in video_data and isinstance(video_data['tags'], list):
        video_tags = [str(tag).lower() for tag in video_data['tags']]
    
    text_to_check = f"{video_url} {video_user} {' '.join(video_tags)}"
    
    # 3. الفحص الشامل
    for keyword in exclude_keywords:
        if keyword.lower() in text_to_check:
            # استثناء ذكي: الحرمين الشريفين والمساجد قد تحتوي على مصلين (عشوائياً في الخلفية)
            # لذا سنسمح بكلمات مثل people/man فقط في هذا السياق، طالما لم توجد كلمات غير مناسبة أخرى
            if scene_name in ["المدينة المنورة", "مساجد"] and keyword.lower() in ["people", "person", "man", "crowd", "human"]:
                continue
                
            print(f"   🛡️ Smart Filter: استبعاد فيديو ID {video_data.get('id')} بسبب كلمة محظورة: '{keyword}'")
            return False
    
    return True




def download_nature_clips(scene_names: list, target_duration: float, output_dir: str = None, abort_event: threading.Event = None) -> list:
    """
    Downloads multiple random nature video clips from Pexels until total duration is met.
    """
    import tempfile
    if output_dir is None:
        output_dir = os.path.join(tempfile.gettempdir(), "quran_video_snippets")
    
    if not PEXELS_API_KEY:
        print("❌ Warning: PEXELS_API_KEY not set. Using color background instead.")
        return []

    import random
    import math

    # نحن نستخدم انتقال (Transition) مدته ثانية واحدة بين المقاطع
    # لذا كل مقطع يضيف فعلياً 4 ثوانٍ من الوقت الجديد (ما عدا الأول)
    clips_needed = math.ceil(target_duration / 4.0) + 1 
    print(f"🎯 Need {clips_needed} clips for {target_duration}s video (with transitions)")

    all_video_options = []
    seen_ids = set()
    
    # 2. Collect video candidates for all selected scenes
    # We will try to fetch enough unique videos to fill the duration without looping
    for scene in scene_names:
        if abort_event and abort_event.is_set():
            raise RequestAborted("Aborted during download search.")
        keyword = SCENE_KEYWORDS.get(scene, scene)
        print(f"🎬 Searching Pexels for: {keyword}...")
        
        # Try to fetch unique videos to fill the duration
        # First, do a search to get metadata (total results)
        try:
            headers = {"Authorization": PEXELS_API_KEY}
            params = {
                "query": keyword, 
                "orientation": "portrait", 
                "size": "medium", 
                "per_page": 1, # Minimal fetch to get total_results
                "page": 1
            }
            initial_resp = requests.get("https://api.pexels.com/videos/search", headers=headers, params=params, timeout=10)
            total_results = 0
            if initial_resp.status_code == 200:
                total_results = initial_resp.json().get("total_results", 0)
            
            print(f"     Found {total_results} total videos for '{keyword}'")
            
            if total_results == 0:
                print(f"   ⚠️ No videos found for {keyword}")
                continue

            # Decide which pages to fetch
            # We want random pages, but within valid range.
            # Max videos per page is usually up to 80. Let's use 20.
            per_page = 20
            max_pages = math.ceil(total_results / per_page)
            # Cap at 50 pages to avoid deep pagination issues or bad relevance
            max_searchable_page = min(max_pages, 50)
            
            # Select random pages to fetch. Always include page 1 for best relevance if total results are low.
            pages_to_fetch = {1}
            while len(pages_to_fetch) < 4 and len(pages_to_fetch) < max_searchable_page:
                pages_to_fetch.add(random.randint(1, max_searchable_page))
            
            print(f"   🔎 Will fetch pages: {pages_to_fetch}")
            
            for page_num in pages_to_fetch:
                if abort_event and abort_event.is_set():
                    raise RequestAborted("Aborted during page fetch.")

                if len(all_video_options) >= clips_needed * 2:
                     break

                print(f"   📥 Fetching page {page_num}...")
                params["page"] = page_num
                params["per_page"] = per_page
                
                response = requests.get("https://api.pexels.com/videos/search", headers=headers, params=params, timeout=15)
                
                if response.status_code == 200:
                    data = response.json()
                    videos = data.get("videos", [])
                    
                    for v in videos:
                        vid_id = v.get('id')
                        if vid_id in seen_ids:
                            continue
                        
                        # ✅ فحص ملاءمة الفيديو قبل المعالجة
                        if not is_video_appropriate(v, scene):
                            continue
                            
                        # Find a good download URL
                        video_files = v.get("video_files", [])
                        best_url = None
                        for vf in video_files:
                            # Prefer HD portrait (ht > wd), but ensure height >= 720 for quality
                            if vf.get("width", 0) < vf.get("height", 0) and vf.get("height", 0) >= 720:
                                best_url = vf.get("link")
                                break
                        
                        # Fallback 1: Any portrait
                        if not best_url:
                             for vf in video_files:
                                if vf.get("width", 0) < vf.get("height", 0):
                                    best_url = vf.get("link")
                                    break

                        # Fallback 2: First available
                        if not best_url and video_files:
                            best_url = video_files[0].get("link")
                        
                        if best_url:
                            seen_ids.add(vid_id)
                            all_video_options.append({
                                'url': best_url,
                                'id': vid_id,
                                'duration': v.get('duration', 10)
                            })
                else:
                    print(f"   ❌ Pexels API Error on page {page_num}: {response.status_code}")

        except Exception as e:
            print(f"   ❌ Error searching for {scene}: {e}")
            continue
            
    if not all_video_options:
        print("⚠️ No videos found for any selected scenes. Using falbacks.")
        return []

    # 3. Shuffle and pick exactly the number of clips needed
    # If we have enough, this ensures no repeats. If not, we'll have to repeat later in generation.
    random.shuffle(all_video_options)
    
    selected_options = all_video_options[:clips_needed]
    
    # If we still don't have enough to fill duration, likely we will loop in generate_final_video,
    # OR we can duplicate here to ensure we have list length matching clips_needed
    while len(selected_options) < clips_needed and len(selected_options) > 0:
        selected_options.append(random.choice(selected_options))
    
    downloaded_paths = []
    os.makedirs(output_dir, exist_ok=True)

    print(f"🏗️ Streaming {len(selected_options)} diverse 5-second snippets...")
    
    for i, v_info in enumerate(selected_options):
        if abort_event and abort_event.is_set():
            raise RequestAborted("Aborted during download stream.")
        
        video_id = v_info['id']
        download_url = v_info['url']
        local_path = os.path.join(output_dir, f"snippet_{i}_{video_id}.mp4")
        
        # USE FFMPEG TO DOWNLOAD ONLY 5 SECONDS
        # This saves massive bandwidth and space!
        try:
            print(f"⚡ Streaming snippet {i+1}/{len(selected_options)} from Pexels...")
            subprocess.run([
                'ffmpeg', '-ss', '0', '-t', '5', 
                '-i', download_url, 
                '-c:v', 'libx264', '-preset', 'ultrafast', '-an', # No audio, fast encode
                '-y', local_path
            ], check=True, capture_output=True)
            
            if os.path.exists(local_path):
                downloaded_paths.append(local_path)
        except Exception as e:
            print(f"❌ Failed to stream snippet {video_id}: {e}")
            continue
    
    return downloaded_paths



# تحسين الأداء: تتبع حالة الخطوط لمنع الفحص المتكرر
FONTS_DONE = False

def ensure_fonts_downloaded():
    """
    تأكد من وجود الخطوط الأساسية محلياً في مجلد fonts/
    """
    global FONTS_DONE
    if FONTS_DONE:
        return
        
    fonts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'fonts')
    os.makedirs(fonts_dir, exist_ok=True)
    
    required_fonts = {
        "Amiri-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf",
        "NotoKufiArabic-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/notokufiarabic/NotoKufiArabic-Regular.ttf",
        "NotoNaskhArabic-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/notonaskharabic/NotoNaskhArabic-Regular.ttf",
        "Tajawal-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/tajawal/Tajawal-Regular.ttf",
        "NotoNastaliqUrdu-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/notonastaliqurdu/NotoNastaliqUrdu-Regular.ttf",
        "NotoSans-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/notosans/NotoSans-Regular.ttf",
        "ScheherazadeNew-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/scheheradenew/ScheherazadeNew-Regular.ttf",
        "Mushaf-Naskh.ttf": "https://github.com/quran/quran-fonts/raw/master/fonts/KFGQPC_Uthman_Taha_Naskh_Regular.ttf"
    }
    
    for filename, url in required_fonts.items():
        path = os.path.join(fonts_dir, filename)
        
        is_valid = False
        if os.path.exists(path) and os.path.getsize(path) > 1000:
            try:
                with open(path, 'rb') as f:
                    header = f.read(4)
                    if header in [b'\x00\x01\x00\x00', b'OTTO', b'ttcf']:
                        is_valid = True
            except: pass
        
        if not is_valid:
            if os.path.exists(path):
                os.remove(path)
            print(f"⬇️ Downloading missing font: {filename}...")
            try:
                import requests
                response = requests.get(url, timeout=30, stream=True)
                if response.status_code == 200:
                    with open(path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                    print(f"✅ Downloaded {filename}")
            except Exception as e:
                print(f"❌ Error downloading {filename}: {e}")
    
    FONTS_DONE = True

# Call it now that it's defined
ensure_fonts_downloaded()

def find_font_path(font_name):
    """
    Extremely robust font discovery for both Windows and Linux.
    Prioritizes locally downloaded fonts for consistency.
    """
    # Ensure fonts are downloaded first
    ensure_fonts_downloaded()
    
    print(f"🔍 Searching for font: '{font_name}'")
    
    # 1. Check Local Project Fonts First (Highest Priority)
    local_fonts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'fonts')
    
    # Precise mapping to local files
    local_mapping = {
        'Traditional Arabic': 'NotoNaskhArabic-Regular.ttf',
        'Amiri': 'Amiri-Regular.ttf',
        'Amiri-Regular.ttf': 'Amiri-Regular.ttf',
        'Noto Sans Arabic': 'NotoNaskhArabic-Regular.ttf',
        'Noto Naskh Arabic': 'NotoNaskhArabic-Regular.ttf',
        'Noto Kufi Arabic': 'NotoKufiArabic-Regular.ttf',
        'Noto Nastaliq Urdu': 'NotoNastaliqUrdu-Regular.ttf',
        'quran-uthmani': 'Mushaf-Naskh.ttf',
        'quran-tajweed': 'ScheherazadeNew-Regular.ttf',
        'KFGQPC_Uthmanic_Script_HAFS.ttf': 'Mushaf-Naskh.ttf',
        'KFGQPC_Naskh.ttf': 'Mushaf-Naskh.ttf',
        'Uthmanic': 'Mushaf-Naskh.ttf',
        'Naskh': 'Mushaf-Naskh.ttf',
        'Mushaf': 'Mushaf-Naskh.ttf',
        'Tajawal': 'Tajawal-Regular.ttf',
        'Arial': 'NotoSans-Regular.ttf',
        'English': 'NotoSans-Regular.ttf'
    }
    
    # Normalize input
    target_file = local_mapping.get(font_name)
    if not target_file:
         # Fuzzy match
        if 'kufi' in font_name.lower(): target_file = 'NotoKufiArabic-Regular.ttf'
        elif 'naskh' in font_name.lower(): target_file = 'NotoNaskhArabic-Regular.ttf'
        elif 'amiri' in font_name.lower(): target_file = 'Amiri-Regular.ttf'
        elif 'nastaliq' in font_name.lower(): target_file = 'NotoNastaliqUrdu-Regular.ttf'
        elif 'tajawal' in font_name.lower(): target_file = 'Tajawal-Regular.ttf'

    if target_file:
        local_path = os.path.join(local_fonts_dir, target_file)
        if os.path.exists(local_path):
             print(f"✅ LOCAL HIT: {font_name} -> {local_path}")
             return local_path
        
        # محاولة أخيرة: إذا لم يجد الملف المطلوب، ابحث عن بديل Naskh
        if 'Naskh' in target_file or 'Uthman' in target_file:
            alt_path = os.path.join(local_fonts_dir, 'Mushaf-Naskh.ttf')
            if os.path.exists(alt_path):
                print(f"✅ ALT HIT: Using Mushaf-Naskh.ttf as alternative")
                return alt_path
        
        print(f"❌ File not found at: {local_path}")
            # Do not return broken path, let it try other fallbacks

    # 2. Hardcoded System Paths (Linux/System Fallback)
    linux_direct_paths = {
        'Traditional Arabic': [
            "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoNaskh-Regular.ttf",
            "/usr/share/fonts/truetype/amiri/amiri-regular.ttf"
        ],
        'Amiri': [
            "/usr/share/fonts/truetype/amiri/amiri-regular.ttf",
            "/usr/share/fonts/opentype/amiri/amiri-regular.otf",
            "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf"
        ],
        'Noto Sans Arabic': [
            "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"
        ],
        'English': [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
        ],
        'Arial': [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            "C:\\Windows\\Fonts\\arial.ttf"
        ]
    }

    # Check direct paths first
    if font_name in linux_direct_paths:
        for path in linux_direct_paths[font_name]:
            if os.path.exists(path):
                print(f"🎯 DIRECT HIT: {font_name} -> {path}")
                return path

    # 2. Dynamic Search
    font_dirs = [
        "C:\\Windows\\Fonts",
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        os.path.expanduser("~/.local/share/fonts"),
        "/app/fonts",
        # Add local project fonts directory
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'fonts')
    ]

    font_mapping = {
        'Noto Naskh Arabic': ['NotoNaskhArabic-Regular.ttf', 'NotoSansArabic-Regular.ttf'],
        'Noto Kufi Arabic': ['NotoKufiArabic-Regular.ttf', 'NotoSansArabic-Regular.ttf'],
        'Noto Sans Arabic': ['NotoSansArabic-Regular.ttf', 'NotoNaskhArabic-Regular.ttf'],
        'Noto Nastaliq Urdu': ['NotoNastaliqUrdu-Regular.ttf', 'NotoNaskhArabic-Regular.ttf'],
        'Traditional Arabic': ['NotoNaskhArabic-Regular.ttf', 'trado.ttf', 'Amiri-Regular.ttf'],
        'Amiri': ['Amiri-Regular.ttf', 'amiri.ttf', 'NotoNaskhArabic-Regular.ttf'],
        'Simplified Arabic': ['Simplified Arabic.ttf', 'simpo.ttf', 'NotoSansArabic-Regular.ttf'],
        'Arial': ['arial.ttf', 'DejaVuSans.ttf', 'FreeSans.ttf']
    }

    candidates = font_mapping.get(font_name, [font_name])
    search_list = []
    for c in candidates:
        search_list.append(c.lower())
        if not c.lower().endswith(('.ttf', '.otf')):
            search_list.append(f"{c.lower()}.ttf")

    for fdir in font_dirs:
        if not os.path.exists(fdir): continue
        for root, _, files in os.walk(fdir):
            files_lower = {f.lower(): f for f in files}
            for target in search_list:
                if target in files_lower:
                    path = os.path.join(root, files_lower[target])
                    print(f"✅ DYNAMIC MATCH: {font_name} -> {path}")
                    return path

    # 3. Emergency Fallback
    fallbacks = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", # Good for English
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "C:\\Windows\\Fonts\\arial.ttf"
    ]
    for fb in fallbacks:
        if os.path.exists(fb):
            print(f"⚠️ FALLBACK: {fb}")
            return fb

    return None





def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

def calculate_ayah_chunks(words, translation, fontsize, font_family, show_arabic, show_english):
    """
    تقسيم الآية إلى أجزاء (Chunks) بحيث لا يتجاوز كل جزء سطرين عربي وسطرين إنجليزي.
    """
    import math
    from PIL import Image, ImageDraw, ImageFont
    import arabic_reshaper

    # 1. إعداد الخطوط والقياس (مماثل لـ create_ayah_text_clip)
    W, H = 1080, 1920
    configuration = {'delete_harakat': False, 'support_ligatures': True}
    reshaper = arabic_reshaper.ArabicReshaper(configuration=configuration)
    
    actual_font_family = "Amiri"
    if font_family == "quran-uthmani": actual_font_family = "KFGQPC_Uthmanic_Script_HAFS.ttf"
    elif "Mushaf" in font_family or "Naskh" in font_family: actual_font_family = "KFGQPC_Naskh.ttf"
    elif font_family == "quran-tajweed": actual_font_family = "ScheherazadeNew-Regular.ttf"
    elif "simple" in font_family or "wordbyword" in font_family: actual_font_family = "Amiri"
    else: actual_font_family = font_family
    
    arabic_font_path = find_font_path(actual_font_family) or find_font_path("Noto Naskh Arabic")
    english_font_path = find_font_path("Arial") or find_font_path("DejaVu Sans")
    
    try:
        font_arabic = ImageFont.truetype(arabic_font_path, fontsize) if arabic_font_path else ImageFont.load_default()
        font_english = ImageFont.truetype(english_font_path, int(fontsize * 0.7)) if english_font_path else ImageFont.load_default()
    except:
        font_arabic = font_english = ImageFont.load_default()

    temp_img = Image.new('RGBA', (W, H))
    tmp_draw = ImageDraw.Draw(temp_img)
    def measure_text(text, font):
        bbox = tmp_draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]

    # 2. تقسيم السطور العربية
    arabic_lines_words = []
    if show_arabic:
        max_w = W * 0.9
        current_line = []
        current_w = 0
        space_w = measure_text(" ", font_arabic)[0]
        for w in words:
            rw = reshaper.reshape(w)
            w_w, _ = measure_text(rw, font_arabic)
            if current_w + w_w > max_w and current_line:
                arabic_lines_words.append(current_line)
                current_line = [w]
                current_w = w_w
            else:
                current_line.append(w)
                current_w += (w_w + space_w)
        if current_line: arabic_lines_words.append(current_line)

    # 3. تقسيم السطور الإنجليزية
    english_lines_text = []
    if show_english and translation:
        max_eng_w = W * 0.8
        eng_words_list = translation.split()
        current_line = []
        current_w = 0
        space_w = measure_text(" ", font_english)[0]
        for w in eng_words_list:
            w_w, _ = measure_text(w, font_english)
            if current_w + w_w > max_eng_w and current_line:
                english_lines_text.append(" ".join(current_line))
                current_line = [w]
                current_w = w_w
            else:
                current_line.append(w)
                current_w += (w_w + space_w)
        if current_line: english_lines_text.append(" ".join(current_line))

    # 4. تجميع الأجزاء (Chunks) - بحد أقصى سطرين
    num_chunks_ar = math.ceil(len(arabic_lines_words) / 2)
    num_chunks_en = math.ceil(len(english_lines_text) / 2)
    total_chunks = max(num_chunks_ar, num_chunks_en, 1)

    chunks = []
    for i in range(total_chunks):
        # توزيع السطور العربية
        chunk_words = []
        ar_start = i * 2 if num_chunks_ar >= total_chunks else int(i * (len(arabic_lines_words)/total_chunks))
        ar_end = (i + 1) * 2 if num_chunks_ar >= total_chunks else int((i + 1) * (len(arabic_lines_words)/total_chunks))
        if ar_start < len(arabic_lines_words):
            for line in arabic_lines_words[ar_start:ar_end]:
                chunk_words.extend(line)
        
        # توزيع السطور الإنجليزية
        chunk_trans_lines = []
        en_start = i * 2 if num_chunks_en >= total_chunks else int(i * (len(english_lines_text)/total_chunks))
        en_end = (i + 1) * 2 if num_chunks_en >= total_chunks else int((i + 1) * (len(english_lines_text)/total_chunks))
        if en_start < len(english_lines_text):
            chunk_trans_lines = english_lines_text[en_start:en_end]
        
        chunk_trans = " ".join(chunk_trans_lines)
        
        # إذا لم يكن هناك كلمات عربية لهذا الجزء (في حال كان التقسيم غير متوازٍ)، نأخذ كلمات من الجزء السابق
        if not chunk_words and words: chunk_words = words[-max(1, len(words)//total_chunks):]
        
        chunks.append({
            'words': chunk_words,
            'translation': chunk_trans,
            # تحسين الوزن الزمني: طول الكلمات الحقيقي + تعويض للتشكيل
            'weight': sum(len(w) for w in chunk_words) + (len(chunk_words) * 2) 
        })

    return chunks

def render_ayah_layout(words: list, translation: str, fontsize: int, font_family: str, show_arabic: bool, show_english: bool, text_color: str, highlight_color: str, highlight_opacity: float):
    """
    محرك الرندر الاحترافي: يقوم بحساب التنسيق وتوليد لوحة نصية عالية الجودة (PNG).
    """
    W, H = 1080, 1920
    reshaper_cfg = {
        'delete_harakat': False,
        'delete_tatweel': False,
        'support_ligatures': True,
        'preserve_whitespace_at_end': True,
        'use_unshaped_instead_of_isolated': True
    }
    reshaper = arabic_reshaper.ArabicReshaper(reshaper_cfg)
    
    # اختيار الخط المناسب
    actual_font_family = "Amiri-Regular.ttf"
    if font_family == "quran-uthmani":
        actual_font_family = "KFGQPC_Uthmanic_Script_HAFS.ttf"
    elif "Mushaf" in font_family or "Naskh" in font_family:
        actual_font_family = "KFGQPC_Naskh.ttf"
    elif font_family == "quran-tajweed":
        actual_font_family = "ScheherazadeNew-Regular.ttf"
    elif "simple" in font_family:
        actual_font_family = "Amiri-Regular.ttf"
    else:
        actual_font_family = font_family

    arabic_font_path = find_font_path(actual_font_family) or find_font_path("Amiri-Regular.ttf")
    english_font_path = find_font_path("Arial") or find_font_path("NotoSans-Regular.ttf")
    
    try:
        font_arabic = ImageFont.truetype(arabic_font_path, fontsize)
    except:
        font_arabic = ImageFont.load_default()
        
    font_english = None
    if show_english and translation:
        try:
            font_english = ImageFont.truetype(english_font_path, int(fontsize * 0.7))
        except:
            font_english = ImageFont.load_default()

    # إنشاء لوحة مؤقتة للقياس
    dummy_img = Image.new('RGBA', (W, H))
    draw = ImageDraw.Draw(dummy_img)
    
    def measure(text, font):
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]

    # 1. تقسيم الأسطر العربية
    arabic_lines = []
    if show_arabic and words:
        reshaped_words = [reshaper.reshape(w) for w in words]
        max_w = W * 0.9
        curr_line = []
        curr_w = 0
        space_w = measure(" ", font_arabic)[0]
        
        for rw in reshaped_words:
            # تنظيف الكلمة من الفراغات الزائدة قبل القياس
            rw = rw.strip()
            w_w, w_h = measure(rw, font_arabic)
            
            if curr_w + w_w > max_w and curr_line:
                # إنهاء السطر الحالي وحساب بيانات الخط الكامل
                # ✅ ملاحظة: لا نعكس الكلمات هنا يدوياً، نترك get_display يتولى الأمر للسطر كاملاً
                line_str = " ".join([w['text'] for w in curr_line])
                arabic_lines.append({
                    'words': curr_line,
                    'full_text': line_str,
                    'w': measure(line_str, font_arabic)[0]
                })
                curr_line = [{'text': rw, 'w': w_w, 'h': w_h}]
                curr_w = w_w
            else:
                curr_line.append({'text': rw, 'w': w_w, 'h': w_h})
                # إضافة الكلمة مع مراعاة مسافة الفراغ الطبيعي
                curr_w += (w_w + space_w)
                
        if curr_line:
            line_str = " ".join([w['text'] for w in curr_line])
            arabic_lines.append({
                'words': curr_line,
                'full_text': line_str,
                'w': measure(line_str, font_arabic)[0]
            })

    # 2. تقسيم الأسطر الإنجليزية
    english_lines = []
    if show_english and translation:
        max_e_w = W * 0.85
        e_words = translation.split()
        curr_line = []
        curr_w = 0
        space_w = measure(" ", font_english)[0]
        for ew in e_words:
            w_w, w_h = measure(ew, font_english)
            if curr_w + w_w > max_e_w and curr_line:
                english_lines.append({'text': " ".join(curr_line), 'w': curr_w, 'h': w_h})
                curr_line = [ew]
                curr_w = w_w
            else:
                curr_line.append(ew)
                curr_w += (w_w + space_w if curr_w > 0 else w_w)
        if curr_line: english_lines.append({'text': " ".join(curr_line), 'w': curr_w, 'h': measure(" ".join(curr_line), font_english)[1]})

    return {
        'arabic_lines': arabic_lines,
        'english_lines': english_lines,
        'font_arabic': font_arabic,
        'font_english': font_english,
        'reshaper': reshaper,
        'W': W, 'H': H
    }

def create_ayah_text_clip(words: list, translation: str = "", duration: float = 5.0, fontsize: int = 50, position: str = 'center', show_highlight: bool = True, highlight_color: str = "#282828", highlight_opacity: float = 0.8, show_arabic: bool = True, show_english: bool = False, font_family: str = "Noto Sans Arabic", text_color: str = "#ffffff", top_text: str = ""):
    """
    محرك الرندر المطور: يعتمد على التحويل المسبق لصور PNG لزيادة السرعة والدقة.
    """
    try:
        print(f"🎨 create_ayah_text_clip: words={len(words)}, font={font_family}, size={fontsize}, color={text_color}, pos={position}, bg={highlight_color}/{highlight_opacity}")
        layout = render_ayah_layout(words, translation, fontsize, font_family, show_arabic, show_english, text_color, highlight_color, highlight_opacity)
        W, H = layout['W'], layout['H']
        font_arabic = layout['font_arabic']
        font_english = layout['font_english']
        
        # التنسيق المطور للكلمات لضمان استواء السطر
        word_spacing = fontsize // 5
        line_height = fontsize * 2.3
        baseline_offset = fontsize * 1.5

        # حساب الطول الكلي للمحتوى لتحديد نقطة البداية Y بناءً على التباعد الجديد
        arabic_h = len(layout['arabic_lines']) * line_height if layout['arabic_lines'] else 0
        english_h = len(layout['english_lines']) * (fontsize * 0.7) * 1.4 if layout['english_lines'] else 0
        gap = 50 if (arabic_h and english_h) else 0
        total_h = arabic_h + english_h + gap
        
        if position == 'top': start_y = H * 0.2
        elif position == 'bottom': start_y = H * 0.8 - total_h
        else: start_y = (H - total_h) / 2

        rgb_text = hex_to_rgb(text_color)
        rgb_highlight = hex_to_rgb(highlight_color)
        alpha_highlight = int(highlight_opacity * 255)

        # تجهيز قائمة ببيانات الكلمات للهايلايت
        all_words_meta = []
        curr_y = start_y
        
        # 1. رندر النص الثابت (مرة واحدة فقط) لتوفير الوقت
        static_img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        d_static = ImageDraw.Draw(static_img)

        # رسم النص العلوي (البسملة أو السورة)
        if top_text:
            # ✅ حل مشكلة "ةروس" للنص العلوي
            from PIL import features
            has_raqm = features.check('raqm')
            
            if has_raqm:
                # القاعدة الذهبية: مع راقم، نرسل النص الخام وهو يتولى الربط والاتجاه
                top_visual = top_text
                top_draw_args = {"font": font_arabic, "anchor": "mm", "direction": "rtl"}
            else:
                # إذا لم يوجد راقم (بيئات بدائية)، نضطر للتشكيل والعكس اليدوي
                reshaped_top = layout['reshaper'].reshape(top_text)
                top_visual = get_display(reshaped_top)
                top_draw_args = {"font": font_arabic, "anchor": "mm"}
            
            # Shadow
            d_static.text((W/2+2, H*0.14+2), top_visual, fill=(0,0,0,180), **top_draw_args)
            # Main text
            d_static.text((W/2, H*0.14), top_visual, fill=(*rgb_text, 255), **top_draw_args)

        for line_idx, line_data in enumerate(layout['arabic_lines']):
            line_str = line_data['full_text']
            line_w = line_data['w']
            
            # ✅ حل مشكلة "ةروس": التحقق من وجود دعم الـ RTL في مكتبة الصور
            from PIL import features
            has_raqm = features.check('raqm')
            
            if has_raqm:
                # إذا كان السيرفر يدعم RTL (غالباً في Linux)، نرسل النص مشكلاً فقط ونترك المكتبة تحدد الاتجاه
                visual_line = line_str
                text_dir = 'rtl'
            else:
                # إذا كان السيرفر لا يدعم RTL (مثل Windows العادي)، نستخدم العكس اليدوي
                visual_line = get_display(line_str)
                text_dir = None
            
            # حساب الإحداثيات الأساسية للسطر
            baseline_y = curr_y + baseline_offset
            center_x = W / 2
            
            # رسم السطر بالكامل
            # ملاحظة: نستخدم direction='rtl' فقط إذا كان raqm متاحاً
            draw_args = {"font": font_arabic, "fill": (0,0,0,160), "anchor": "ms"}
            if has_raqm: draw_args["direction"] = "rtl"
            
            d_static.text((center_x + 2, baseline_y + 2), visual_line, **draw_args)
            draw_args["fill"] = (*rgb_text, 255)
            d_static.text((center_x, baseline_y), visual_line, **draw_args)
            
            # حساب مواقع الكلمات للهايلايت بدقة داخل السطر
            current_x_offset = center_x + (line_w / 2)
            
            for w in line_data['words']:
                # لتحديد مكان الهايلايت، نحتاج دائماً للنسخة المرئية للكلمة
                visual_w = get_display(w['text'])
                
                # تصحيح يدوي للأقواس لضمان ظهورها بشكل ﴿7﴾
                import re
                match = re.search(r'(\d+)', visual_w)
                if match:
                    # في بيئات Linux، أحياناً يكون العكس المسبق للقوس ضاراً، لذا نجرب التنسيق الأبسط
                    num = match.group(1)
                    if has_raqm: visual_w = f"({num})" # Raqm سيعوض الأقواس تلقائياً
                    else: visual_w = f"﴾{num}﴿"
                
                # تخزين بيانات التظليل
                # نستخدم قياس الكلمة الفعلي لضمان أن التظليل يغطي الحروف بدقة
                all_words_meta.append({
                    'line_idx': line_idx,
                    'y': baseline_y - (fontsize * 1.05),
                    'h': fontsize * 1.5,
                    'cx': current_x_offset - (w['w'] / 2),
                    'w': w['w']
                })
                # الانتقال للكلمة التالية (مع إضافة فراغ بسيط للموازنة)
                current_x_offset -= (w['w'] + (fontsize // 5))
            
            curr_y += line_height

        curr_y += gap
        for line in layout['english_lines']:
            cx, cy = W/2, curr_y + line['h']/2
            d_static.text((cx+2, cy+2), line['text'], font=font_english, fill=(0,0,0,140), anchor="mm")
            d_static.text((cx, cy), line['text'], font=font_english, fill=(255,255,255,255), anchor="mm")
            curr_y += (fontsize * 0.7) * 1.4

        static_np = np.array(static_img.convert('RGB'))
        # Create mask from alpha channel
        static_mask_img = static_img.split()[-1]
        static_mask_np = np.array(static_mask_img) / 255.0

        def make_txt_frame(t): return static_np
        def make_txt_mask(t): return static_mask_np

        txt_clip = create_video_clip(make_txt_frame, duration=duration)
        txt_mask = create_video_clip(make_txt_mask, duration=duration, is_mask=True)
        txt_clip.mask = txt_mask

        if show_highlight and all_words_meta:
            def combined_frame(t):
                # Start with a transparent image
                img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
                d = ImageDraw.Draw(img)
                
                # Draw Highlight First (Background layer)
                progress = (t / duration) * len(all_words_meta)
                # Draw Highlight First (Background layer)
                progress = (t / duration) * len(all_words_meta)
                center_x = W / 2
                for line_idx, line_data in enumerate(layout['arabic_lines']):
                    line_words = [w for w in all_words_meta if w['line_idx'] == line_idx]
                    if not line_words: continue
                    line_start_idx = all_words_meta.index(line_words[0])
                    revealed = 0
                    if progress >= line_start_idx + len(line_words): revealed = len(line_words)
                    elif progress > line_start_idx: revealed = progress - line_start_idx
                    
                    if revealed > 0:
                        # تصحيح عرض الخلفية ليكون مساوياً تماماً لعرض السطر المقاس
                        line_w = line_data['w']
                        line_right = center_x + (line_w / 2) + 25
                        line_left_full = center_x - (line_w / 2) - 25
                        total_width = line_right - line_left_full
                        
                        curr_width = total_width * (revealed / len(line_words))
                        first_w = line_words[0]
                        d.rounded_rectangle([line_right - curr_width, first_w['y']-10, line_right, first_w['y'] + first_w['h'] + 10], radius=30, fill=(*rgb_highlight, alpha_highlight))
                
                # Draw Static Text (Foreground layer)
                img.paste(static_img, (0, 0), static_img)
                return np.array(img.convert('RGB'))

            def combined_mask(t):
                m_img = Image.new('L', (W, H), 0)
                md = ImageDraw.Draw(m_img)
                
                # Alpha from Highlight
                progress = (t / duration) * len(all_words_meta)
                center_x = W / 2
                for line_idx, line_data in enumerate(layout['arabic_lines']):
                    line_words = [w for w in all_words_meta if w['line_idx'] == line_idx]
                    if not line_words: continue
                    line_start_idx = all_words_meta.index(line_words[0])
                    revealed = 0
                    if progress >= line_start_idx + len(line_words): revealed = len(line_words)
                    elif progress > line_start_idx: revealed = progress - line_start_idx
                    if revealed > 0:
                        line_w = line_data['w']
                        line_right = center_x + (line_w / 2) + 25
                        line_left_full = center_x - (line_w / 2) - 25
                        total_width = line_right - line_left_full
                        curr_width = total_width * (revealed / len(line_words))
                        first_w = line_words[0]
                        md.rounded_rectangle([line_right - curr_width, first_w['y']-10, line_right, first_w['y'] + first_w['h'] + 10], radius=30, fill=alpha_highlight)
                
                # Add Alpha from Static Text
                m_img.paste(static_mask_img, (0, 0), static_mask_img)
                return np.array(m_img) / 255.0

            final_txt_clip = create_video_clip(combined_frame, duration=duration)
            final_txt_mask = create_video_clip(combined_mask, duration=duration, is_mask=True)
            final_txt_clip.mask = final_txt_mask
            return final_txt_clip
        
        return txt_clip

    except Exception as e:
        import traceback
        print(f"❌ create_ayah_text_clip failed: {traceback.format_exc()}")
        return None

def create_metadata_overlay(text: str, duration: float, position: str = "top-right", font_size: int = 30):
    """
    أنشئ ملصق نصي صغير (اسم القارئ أو السورة) في زاوية الفيديو بدون خلفية ومع دعم كامل لجميع الحروف.
    """
    try:
        W, H = 1080, 1920
        # نستخدم هنا Amiri لأنه الخط الأكثر ثباتاً في دعم الحروف والتشكيل لتجنب المربعات
        font_path = find_font_path("Amiri") or find_font_path("Noto Naskh Arabic")
        
        # إعدادات معالجة النصوص العربية لضمان توافق الحروف المعقدة
        configuration = {
            'delete_harakat': False,
            'support_ligatures': True,
            'use_unshaped_instead_of_isolated': True
        }
        reshaper = arabic_reshaper.ArabicReshaper(configuration=configuration)
        reshaped_text = reshaper.reshape(text)
        
        def safe_draw_text(draw_obj, txt, x, y, font, fill):
            try:
                # التعديل النهائي لأسماء السور والقراء
                visual_text = get_display(txt)
                draw_obj.text((x, y), visual_text, font=font, fill=fill, anchor="mm")
            except:
                draw_obj.text((x, y), txt, font=font, fill=fill, anchor="mm")

        def make_frame(t):
            img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            
            try:
                font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
            except:
                font = ImageFont.load_default()
            
            # قياس النص بدقة
            bbox = draw.textbbox((0, 0), reshaped_text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            
            # تحديد الموقع بناءً على قياس النص الفعلي
            margin_x = 60
            margin_y = 120 # Doubled margin from top
            if position == "top-right":
                cx, cy = W - tw / 2 - margin_x, margin_y + th / 2
            else:
                cx, cy = margin_x + tw / 2, margin_y + th / 2
            
            # رسم الظل (Shadow) لضمان الوضوح بدون خلفية سوداء
            safe_draw_text(draw, reshaped_text, cx + 2, cy + 2, font, (0, 0, 0, 200))
            
            # رسم النص الأساسي باللون الأبيض الناصع
            safe_draw_text(draw, reshaped_text, cx, cy, font, (255, 255, 255, 255))
            
            return np.array(img.convert('RGB'))

        def make_mask(t):
            mask = Image.new('L', (W, H), 0)
            draw = ImageDraw.Draw(mask)
            
            try:
                font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
            except:
                font = ImageFont.load_default()
                
            bbox = draw.textbbox((0, 0), reshaped_text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            
            margin_x = 60
            margin_y = 120 # Doubled margin from top
            if position == "top-right":
                cx, cy = W - tw / 2 - margin_x, margin_y + th / 2
            else:
                cx, cy = margin_x + tw / 2, margin_y + th / 2
                
            # رسم قناع النص وظله
            safe_draw_text(draw, reshaped_text, cx + 2, cy + 2, font, 150)
            safe_draw_text(draw, reshaped_text, cx, cy, font, 255)
            
            return np.array(mask) / 255.0

        try:
            overlay_clip = create_video_clip(make_frame, duration=duration)
            mask_clip = create_video_clip(make_mask, duration=duration, is_mask=True)
        except Exception as e:
            print(f"Fallback in metadata overlay: {e}")
            overlay_clip = create_video_clip(make_frame, duration=duration)
            mask_clip = create_video_clip(make_mask, duration=duration, is_mask=True)
        mask_clip.is_mask = True
        overlay_clip.mask = mask_clip
        
        return overlay_clip
    except Exception as e:
        print(f"⚠️ Error creating metadata overlay (fixed): {e}")
        return None



def generate_final_video(audio_path: str, video_scenes: list, output_path: str, duration: float, synced_data: list = None, font_size: int = 50, show_highlight: bool = True, highlight_color: str = "#282828", highlight_opacity: float = 0.8, position: str = "center", show_arabic: bool = True, show_english: bool = False, font_family: str = "Noto Sans Arabic", text_color: str = "#ffffff", abort_event: threading.Event = None, display_mode: str = "ayah", top_text: str = "", show_video_overlay: bool = False, background_type: str = "video", reciter_name: str = "", surah_name: str = ""):
    print(f"🎬 generate_final_video called with: font_size={font_size}, show_highlight={show_highlight}, highlight_color={highlight_color}, position={position}, show_arabic={show_arabic}, show_english={show_english}, font_family={font_family}, text_color={text_color}")
    
    if abort_event and abort_event.is_set():
        raise RequestAborted("Aborted at start of generation.")
        
    audio_clip = AudioFileClip(audio_path)

    w, h = 1080, 1920
    background_clips = []
    video_paths = []
    image_paths = []
    
    transition_duration = 1.0
    if background_type == "image":
        image_paths = download_nature_images(video_scenes, duration, abort_event=abort_event)
        if image_paths:
            print(f"🖼️ Processing {len(image_paths)} images for background...")
            num_imgs = len(image_paths)
            # حساب مدة كل صورة مع مراعاة التداخل (Overlaps)
            # Duration = N*d - (N-1)*t  => d = (Duration + (N-1)*t) / N
            img_duration = (duration + (num_imgs - 1) * transition_duration) / num_imgs
            for i, path in enumerate(image_paths):
                try:
                    clip = ImageClip(path)
                    clip = set_duration(clip, img_duration)
                    clip = resize_clip(clip, height=h)
                    if clip.w > w:
                        x_center = clip.w / 2
                        try:
                            clip = clip.cropped(x_center=x_center, width=w)
                        except TypeError:
                            # MoviePy 1.x or alternative 2.x signature
                            if hasattr(clip, 'cropped'):
                                clip = clip.cropped(center_x=x_center, width=w)
                            else:
                                clip = clip.crop(x_center=x_center, width=w)
                    elif clip.w < w:
                        clip = resize_clip(clip, width=w)
                    
                    # تأثير زووم خفيف (Ken Burns) لإعطاء حياة للصورة
                    clip = apply_zoom(clip, img_duration)

                    if i > 0:
                        clip = apply_crossfade(clip, transition_duration)
                    background_clips.append(clip)
                except Exception as e:
                    print(f"⚠️ Error processing image {path}: {e}")
    else:
        video_paths = download_nature_clips(video_scenes, duration, abort_event=abort_event)
        if video_paths:
            print(f"🎞️ Processing {len(video_paths)} snippets for background...")
            for i, path in enumerate(video_paths):
                try:
                    clip = VideoFileClip(path)
                    if clip.size != (w, h) or clip.duration > 5.1:
                        clip = resize_clip(clip, height=h)
                        if clip.w > w:
                            x_center = clip.w / 2
                            try:
                                clip = clip.cropped(x_center=x_center, width=w)
                            except TypeError:
                                if hasattr(clip, 'cropped'):
                                    clip = clip.cropped(center_x=x_center, width=w)
                                else:
                                    clip = clip.crop(x_center=x_center, width=w)
                        clip = subclip(clip, 0, 5)
                    
                    if i > 0:
                        clip = apply_crossfade(clip, transition_duration)
                    background_clips.append(clip)
                except Exception as e:
                    print(f"⚠️ Skipping broken clip {path}: {e}")
    
    if background_clips:
        # Build composite background with manual overlaps for perfect crossfades
        curr_t = 0
        layered_bg = []
        for i, clip in enumerate(background_clips):
            start_t = curr_t
            if i > 0:
                start_t -= transition_duration
            
            clip = set_start(clip, max(0, start_t))
            layered_bg.append(clip)
            curr_t = start_t + clip.duration
            
        video_clip = CompositeVideoClip(layered_bg, size=(w, h))
        video_clip = set_duration(video_clip, duration)
    else:
        scene_name = video_scenes[0] if video_scenes else "Ocean"
        bg_color = SCENE_COLORS.get(scene_name, (50, 50, 50))
        video_clip = ColorClip(size=(w, h), color=bg_color, duration=duration)

    # --- نظام الرندر السريع بالفيمبيج (FFmpeg Speed Layer V2) ---
    # هذا النظام يتخطى كل مشاكل MoviePy البطيء ويستخدم FFmpeg مباشرة للتجميع النهائي.
    
    temp_render_dir = tempfile.mkdtemp(prefix="final_render_")
    
    # 1. إعداد الطبقات (Dark Overlay + Metadata + Quran)
    final_overlays = []

    # إضافة طبقة تعتيم للفيديو إذا تمت طلبها
    if show_video_overlay:
        dark_overlay = ColorClip(size=(w, h), color=(0, 0, 0), duration=duration)
        if hasattr(dark_overlay, 'set_opacity'):
             dark_overlay = dark_overlay.set_opacity(0.4)
        elif hasattr(dark_overlay, 'with_opacity'):
             dark_overlay = dark_overlay.with_opacity(0.4)
        final_overlays.append({'clip': dark_overlay, 'start': 0, 'duration': duration, 'name': 'dark_overlay'})
    
    # إضافة اسم القارئ والسورة كطبقات ثابتة تبدأ من البداية
    if reciter_name:
        rec_clip = create_metadata_overlay(f"صوت القارئ: {reciter_name}", duration, position="top-right", font_size=42)
        if rec_clip:
            final_overlays.append({'clip': rec_clip, 'start': 0, 'duration': duration, 'name': 'reciter'})
            
    if surah_name:
        surah_clip = create_metadata_overlay(surah_name, duration, position="top-left", font_size=42)
        if surah_clip:
            final_overlays.append({'clip': surah_clip, 'start': 0, 'duration': duration, 'name': 'surah'})

    # تجميع كليبات الآيات
    if synced_data:
        for entry in synced_data:
            words = entry['words']
            translation = entry.get('translation', '')
            start = entry['start']
            ayah_duration = entry['end'] - entry['start']
            
            if display_mode == "chunked":
                chunks = calculate_ayah_chunks(words, translation, font_size, font_family, show_arabic, show_english)
                curr_start = start
                total_w = sum(c['weight'] for c in chunks) or 1
                for chunk in chunks:
                    chunk_dur = (chunk['weight'] / total_w) * ayah_duration
                    txt_clip = create_ayah_text_clip(
                        words=chunk['words'], translation=chunk['translation'], duration=chunk_dur,
                        fontsize=font_size, position=position, show_highlight=show_highlight,
                        highlight_color=highlight_color, show_arabic=show_arabic, show_english=show_english,
                        font_family=font_family, text_color=text_color, top_text=top_text, highlight_opacity=highlight_opacity
                    )
                    if txt_clip:
                        final_overlays.append({'clip': txt_clip, 'start': curr_start, 'duration': chunk_dur, 'name': 'ayah'})
                    curr_start += chunk_dur
            else:
                txt_clip = create_ayah_text_clip(
                    words=words, translation=translation, duration=ayah_duration,
                    fontsize=font_size, position=position, show_highlight=show_highlight,
                    highlight_color=highlight_color, show_arabic=show_arabic, show_english=show_english,
                    font_family=font_family, text_color=text_color, top_text=top_text, highlight_opacity=highlight_opacity
                )
                if txt_clip:
                    final_overlays.append({'clip': txt_clip, 'start': start, 'duration': ayah_duration, 'name': 'ayah'})

    # 2. تصدير الطبقات كـ PNGs شفافة
    print(f"🖼️ Exporting {len(final_overlays)} overlay layers to high-quality PNGs...")
    ffmpeg_inputs = []
    filter_parts = []
    
    # تصدير فيديو الخلفية (سريع جداً لأنه بدون رندر نصوص)
    bg_video_path = os.path.join(temp_render_dir, "bg_no_text.mp4")
    video_clip.write_videofile(bg_video_path, fps=24, codec="libx264", preset="ultrafast", audio=False, logger=None)
    ffmpeg_inputs.append(f'-i "{bg_video_path}"')
    
    last_v = "0:v"
    for idx, overlay in enumerate(final_overlays):
        try:
            p_path = os.path.join(temp_render_dir, f"layer_{idx:03d}.png")
            clip = overlay['clip']
            
            # Take frame at the END to ensure full text visibility
            frame = clip.get_frame(max(0, clip.duration - 0.1))
            img = Image.fromarray(frame).convert("RGBA")
            
            if hasattr(clip, 'mask') and clip.mask:
                mask_f = clip.mask.get_frame(max(0, clip.duration - 0.1))
                mask_i = Image.fromarray((mask_f * 255).astype('uint8')).convert('L')
                img.putalpha(mask_i)
            
            img.save(p_path)
            
            # الحساب الصحيح لرقم المدخل في FFmpeg
            current_input_idx = len(ffmpeg_inputs)
            ffmpeg_inputs.append(f'-i "{p_path}"')
            
            # التسمية البرمجية للطبقة الناتجة
            curr_v = f"v_layer_{current_input_idx}"
            filter_parts.append(f"[{last_v}][{current_input_idx}:v]overlay=0:0:enable='between(t,{overlay['start']},{overlay['start']+overlay['duration']})'[{curr_v}]")
            last_v = curr_v
        except Exception as export_error:
            print(f"⚠️ Failed to export overlay layer {idx}: {export_error}")
            continue

    # إضافة الصوت الأصلي كمدخل أخير
    audio_input_idx = len(ffmpeg_inputs)
    ffmpeg_inputs.append(f'-i "{audio_path}"')
    
    filter_complex = ";".join(filter_parts)
    if not filter_complex: filter_complex = "copy"
    
    print(f"🎬 FFmpeg: Assembling final video (High Speed Mode)...")
    try:
        cmd = [
            'ffmpeg', '-y',
            *ffmpeg_inputs,
            '-filter_complex', f'"{filter_complex}"' if filter_parts else 'copy',
            '-map', f"[{last_v}]" if filter_parts else "0:v",
            '-map', f"{audio_input_idx}:a",
            '-c:v', 'libx264', 
            '-preset', 'faster',        # توازن ممتاز؛ ضغط أذكى وسرعة جيدة
            '-crf', '24',              # جودة ممتازة مع حجم ملف صغير جداً (Target: 20MB for 1min)
            '-maxrate', '5M',          # منع انفجار الحجم في المشاهد المعقدة
            '-bufsize', '10M',
            '-pix_fmt', 'yuv420p',     # ضروري جداً ليعمل الفيديو على الموبايلات
            '-c:a', 'aac', '-b:a', '128k',
            '-movflags', '+faststart', # يسرع بدء تشغيل الفيديو عند فتحه من المتصفح
            f'"{output_path}"'
        ]
        
        # تنفيذ الأمر بشكل مباشر
        full_cmd = " ".join(cmd)
        subprocess.run(full_cmd, shell=True, check=True)
        print(f"✅ Video Generation Completed in seconds!")
    except Exception as e:
        import traceback
        print(f"❌ FFmpeg Batch Failed: {traceback.format_exc()}")
        # Fallback is removed as FFmpeg is now the engine, but we could add a simplified MoviePy fallback if needed.
        # However, without clips_to_composite it's not possible here.
        raise e

    # تنظيف
    try:
        shutil.rmtree(temp_render_dir)
    except: pass
    
    audio_clip.close()
    video_clip.close()
    for overlay in final_overlays:
        try: overlay['clip'].close()
        except: pass

    # Cleanup downloaded clips
    for paths in [video_paths, image_paths]:
        if paths:
            for path in paths:
                try: 
                    if path and os.path.exists(path): os.remove(path)
                except: pass

    return output_path

def generate_preview_video(sample_arabic: str, sample_english: str, nature_scenes: list = None, audio_url: str = None, font_size: int = 50, show_highlight: bool = True, highlight_color: str = "#282828", highlight_opacity: float = 0.8, position: str = "center", show_arabic: bool = True, show_english: bool = False, font_family: str = "Noto Sans Arabic", text_color: str = "#ffffff", display_mode: str = "ayah", top_text: str = "", show_video_overlay: bool = False, background_type: str = "video", reciter_name: str = "مشاري العفاسي", surah_name: str = "الفاتحة") -> str:
    """
    توليد فيديو معاينة حقيقي باستخدام خلفية (فيديو أو صورة) وصوت الشيخ.
    """
    import tempfile
    print(f"🎬 generate_preview_video: font={font_family}, size={font_size}, color={text_color}, bg={background_type}, scenes={nature_scenes}")
    
    W, H = 1080, 1920
    duration = 5.0 # مدة المعاينة 5 ثوانٍ
    bg_clip = None

    # 1. إعداد الخلفية
    transition_duration = 1.0
    if background_type == "image":
        scenes = nature_scenes or ["مساجد"]
        image_paths = download_nature_images(scenes, duration)
        if image_paths:
            try:
                temp_clips = []
                img_dur = (duration + (len(image_paths) - 1) * transition_duration) / len(image_paths)
                for i, path in enumerate(image_paths):
                    c = ImageClip(path)
                    c = set_duration(c, img_dur)
                    c = resize_clip(c, height=H)
                    if c.w > W:
                        x_center = c.w / 2
                        try:
                            c = c.cropped(x_center=x_center, width=W)
                        except:
                            if hasattr(c, 'cropped'):
                                c = c.cropped(center_x=x_center, width=W)
                            else:
                                c = c.crop(x_center=x_center, width=W)
                curr_t = 0
                layered_preview_bg = []
                for i, c in enumerate(temp_clips):
                    start_t = curr_t
                    if i > 0:
                        start_t -= transition_duration
                    c = set_start(c, max(0, start_t))
                    layered_preview_bg.append(c)
                    curr_t = start_t + c.duration
                
                bg_clip = CompositeVideoClip(layered_preview_bg, size=(W, H))
                bg_clip = set_duration(bg_clip, duration)
            except Exception as e:
                print(f"⚠️ Preview background error: {e}")
                pass
    
    if not bg_clip and background_type == "video":
        assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'assets')
        preview_bg_path = os.path.join(assets_dir, 'preview_background.mp4')
        if os.path.exists(preview_bg_path):
            try:
                bg_clip = VideoFileClip(preview_bg_path)
                bg_clip = resize_clip(bg_clip, height=H)
                if bg_clip.w > W:
                    x_center = bg_clip.w / 2
                    try:
                        bg_clip = bg_clip.cropped(x_center=x_center, width=W)
                    except:
                        if hasattr(bg_clip, 'cropped'):
                            bg_clip = bg_clip.cropped(center_x=x_center, width=W)
                        else:
                            bg_clip = bg_clip.crop(x_center=x_center, width=W)
            except: pass

    if not bg_clip:
        bg_clip = ColorClip(size=(W, H), color=(40, 40, 40), duration=duration)

    bg_clip = set_duration(bg_clip, duration)

    # 2. إنشاء نص المعاينة
    words = sample_arabic.split()
    
    if display_mode == "chunked":
        chunks = calculate_ayah_chunks(words, sample_english, font_size, font_family, show_arabic, show_english)
        total_weight = sum(c['weight'] for c in chunks) or 1
        curr_start = 0
        preview_clips = []
        
        for chunk in chunks:
            chunk_duration = duration * (chunk['weight'] / total_weight)
            c_clip = create_ayah_text_clip(
                words=chunk['words'], translation=chunk['translation'], duration=chunk_duration,
                fontsize=font_size, position=position, show_highlight=show_highlight,
                highlight_color=highlight_color, show_arabic=show_arabic,
                show_english=show_english, font_family=font_family, text_color=text_color,
                top_text=top_text, highlight_opacity=highlight_opacity
            )
            if c_clip:
                c_clip = set_start(c_clip, curr_start)
                preview_clips.append(c_clip)
            curr_start += chunk_duration
        txt_clip = CompositeVideoClip(preview_clips) if preview_clips else None
    else:
        txt_clip = create_ayah_text_clip(
            words=words, translation=sample_english, duration=duration,
            fontsize=font_size, position=position, show_highlight=show_highlight,
            highlight_color=highlight_color, show_arabic=show_arabic,
            show_english=show_english, font_family=font_family, text_color=text_color,
            top_text=top_text, highlight_opacity=highlight_opacity
        )
    
    if not txt_clip:
        return None

    # 3. دمج الفيديو
    bg_comp = [set_duration(bg_clip, duration)]
    if show_video_overlay:
        overlay = ColorClip(size=(W, H), color=(0, 0, 0))
        overlay = set_duration(overlay, duration)
        if hasattr(overlay, 'set_opacity'):
            overlay = overlay.set_opacity(0.4)
        elif hasattr(overlay, 'with_opacity'):
            overlay = overlay.with_opacity(0.4)
        bg_comp.append(overlay)
    
    # إضافة ميتاداتا للمعاينة
    if reciter_name:
        display_reciter = f"صوت القارئ: {reciter_name}"
        reciter_clip = create_metadata_overlay(display_reciter, duration, position="top-right", font_size=42)
        if reciter_clip:
            bg_comp.append(reciter_clip)
    
    if surah_name:
        surah_clip = create_metadata_overlay(surah_name, duration, position="top-left", font_size=42)
        if surah_clip:
            bg_comp.append(surah_clip)
    
    final = CompositeVideoClip(bg_comp + [txt_clip])
    
    # 4. إضافة الصوت إذا توفر (صوت البسملة للشيخ المختار)
    if audio_url:
        try:
            # تحميل ملف الصوت المؤقت
            audio_response = requests.get(audio_url, timeout=10)
            if audio_response.status_code == 200:
                fd, audio_tmp = tempfile.mkstemp(suffix=".mp3")
                os.write(fd, audio_response.content)
                os.close(fd)
                
                audio_clip = AudioFileClip(audio_tmp)
                # إذا كان الصوت أطول من 5 ثوانٍ، قصه. إذا كان أقصر، سيظهر صمت في الباقي
                final = set_audio(final, subclip(audio_clip, 0, min(duration, audio_clip.duration)))
        except:
            pass # إذا فشل الصوت، نستمر بالفيديو صامتاً

    fd, output_path = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    
    final.write_videofile(output_path, fps=24, codec="libx264", audio_codec="aac", preset="ultrafast", logger=None)
    
    bg_clip.close()
    txt_clip.close()
    final.close()
    
    return output_path

def generate_preview_image(
    sample_arabic: str, 
    sample_english: str = "", 
    nature_scenes: list = None,
    font_size: int = 50, 
    show_highlight: bool = True, 
    highlight_color: str = "#282828", 
    highlight_opacity: float = 0.8,
    position: str = 'center', 
    show_arabic: bool = True, 
    show_english: bool = False, 
    font_family: str = "Amiri", 
    text_color: str = "#ffffff",
    display_mode: str = "ayah",
    top_text: str = "",
    show_video_overlay: bool = False,
    background_type: str = "video",
    reciter_name: str = "",
    surah_name: str = ""
):
    """
    Generates a high-quality static preview image (JPG) of the video.
    This is extremely fast compared to video generation.
    """
    try:
        W, H = 1080, 1920
        # 1. Create Background
        # We'll use a representative color or a blurred image if possible.
        # For simplicity and speed, we use the first scene color.
        bg_rgb = (30, 30, 30) # Default dark grey
        if nature_scenes and len(nature_scenes) > 0:
            scene = nature_scenes[0]
            bg_rgb = SCENE_COLORS.get(scene, (30, 30, 30))
        
        # Build image
        img = Image.new('RGB', (W, H), bg_rgb)
        
        # Add subtle gradient overlay to look better
        draw = ImageDraw.Draw(img, 'RGBA')
        for y in range(H):
            alpha = int(120 * (y / H)) # Fade to black at bottom
            draw.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
            
        if show_video_overlay:
            # Add a dark overlay like the video has
            draw.rectangle([0, 0, W, H], fill=(0, 0, 0, 100))

        # 2. Use our existing logic to get a text clip, then get one frame
        # This ensures the preview is 100% identical to the final video
        duration = 5.0
        txt_clip = create_ayah_text_clip(
            words=sample_arabic.split(), 
            translation=sample_english, 
            duration=duration,
            fontsize=font_size, 
            position=position, 
            show_highlight=show_highlight,
            highlight_color=highlight_color, 
            highlight_opacity=highlight_opacity,
            show_arabic=show_arabic, 
            show_english=show_english, 
            font_family=font_family, 
            text_color=text_color, 
            top_text=top_text
        )
        
        if txt_clip:
            # Get the mid-frame (at 2.5s) to see the full highlight usually
            frame = txt_clip.get_frame(duration / 2)
            txt_img = Image.fromarray(frame).convert('RGBA')
            
            # Since make_frame returns the combined pixels, we need the mask to blend it properly
            # Actually, MoviePy's get_frame on a clip with a mask returns RGB.
            # BUT we want to composite it over our colored background.
            # So we better get the mask too.
            mask_frame = txt_clip.mask.get_frame(duration / 2)
            mask_img = Image.fromarray((mask_frame * 255).astype('uint8')).convert('L')
            
            # Blend
            img.paste(txt_img, (0, 0), mask_img)
            txt_clip.close()

        # 3. Add Metadata (Reciter/Surah)
        if reciter_name or surah_name:
            if reciter_name:
                rec_clip = create_metadata_overlay(f"صوت القارئ: {reciter_name}", duration, position="top-right", font_size=42)
                if rec_clip:
                    r_frame = rec_clip.get_frame(0)
                    r_mask = rec_clip.mask.get_frame(0)
                    r_img = Image.fromarray(r_frame).convert('RGBA')
                    r_m_img = Image.fromarray((r_mask * 255).astype('uint8')).convert('L')
                    img.paste(r_img, (0, 0), r_m_img)
                    rec_clip.close()
            
            if surah_name:
                sur_clip = create_metadata_overlay(surah_name, duration, position="top-left", font_size=42)
                if sur_clip:
                    s_frame = sur_clip.get_frame(0)
                    s_mask = sur_clip.mask.get_frame(0)
                    s_img = Image.fromarray(s_frame).convert('RGBA')
                    s_m_img = Image.fromarray((s_mask * 255).astype('uint8')).convert('L')
                    img.paste(s_img, (0, 0), s_m_img)
                    sur_clip.close()

        # Save to temp
        fd, output_path = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        img.save(output_path, "JPEG", quality=85)
        
        return output_path
        
    except Exception as e:
        print(f"❌ Error generating preview image: {e}")
        traceback.print_exc()
        return None

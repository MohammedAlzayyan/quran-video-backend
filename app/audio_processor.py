import os
import requests
import subprocess
from pathlib import Path

# Mapping generic reciter names to EveryAyah IDs
RECITER_MAP = {
    "مشاري العفاسي": "Alafasy_128kbps",
    "عبد الباسط عبد الصمد (مرتل)": "Abdul_Basit_Murattal_192kbps",
    "عبد الباسط عبد الصمد (مجود)": "Abdul_Basit_Mujawwad_128kbps",
    "ماهر المعيقلي": "Maher_AlMuaiqly_64kbps",
    "سعد الغامدي": "Ghamadi_40kbps",
    "عبد الرحمن السديس": "Abdurrahmaan_As-Sudais_192kbps",
    "سعود الشريم": "Saood_ash-Shuraym_128kbps",
    "ياسر الدوسري": "Yasser_Ad-Dussary_128kbps",
    "ناصر القطامي": "Nasser_Alqatami_128kbps",
    "أبو بكر الشاطري": "Abu_Bakr_Ash-Shaatree_128kbps",
    "محمود خليل الحصري (مرتل)": "Husary_128kbps",
    "محمود خليل الحصري (مجود)": "Husary_128kbps_Mujawwad",
    "محمود خليل الحصري (المعلم)": "Husary_Muallim_128kbps",
    "محمد صديق المنشاوي (مرتل)": "Minshawi_Murattal_128kbps",
    "محمد صديق المنشاوي (مجود)": "Minshawy_Mujawwad_64kbps",
    "فارس عباد": "Fares_Abbad_64kbps",
    "أحمد بن علي العجمي": "ahmed_ibn_ali_al_ajamy_128kbps",
    "صلاح البدير": "Salah_Al_Budair_128kbps",
    "صلاح بو خاطر": "Salaah_AbdulRahman_Bukhatir_128kbps",
    "محمد أيوب": "Muhammad_Ayyoub_128kbps",
    "محمد جبريل": "Muhammad_Jibreel_128kbps",
    "هاني الرفاعي": "Hani_Rifai_192kbps",
    "علي الحذيفي": "Hudhaify_128kbps",
    "إبراهيم الأخضر": "Ibrahim_Akhdar_32kbps",
    "عبد الله بصفر": "Abdullah_Basfar_192kbps",
    "خالد القحطاني": "Khaalid_Abdullaah_al-Qahtaanee_192kbps",
    "محمد الطبلاوي": "Mohammad_al_Tablaway_128kbps",
    "مصطفى إسماعيل": "Mustafa_Ismail_48kbps",
    "أحمد نعينع": "Ahmed_Neana_128kbps",
    "إدريس أبكر": "Idrees_Abkar_64kbps",
    "عبد الله الجيني": "Abdullaah_3awwaad_Al-Juhaynee_128kbps",
    "عبد الله مطرود": "Abdullah_Matroud_128kbps",
    "أكرم العلقمي": "Akram_AlAlaqimy_128kbps",
    "على جابر": "Ali_Jaber_64kbps",
    "أيمن سويد": "Ayman_Sowaid_64kbps",
    "سهل ياسين": "Sahl_Yassin_128kbps",
    "خليفة الطنيجي": "khalefa_al_tunaiji_64kbps",
    "محمود علي البنا": "mahmoud_ali_al_banna_32kbps",
    "عزيز عليلي": "aziz_alili_128kbps",
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

def get_audio_duration(file_path: str) -> float:
    """Gets duration of an audio file using ffprobe."""
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error', '-show_entries', 
            'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', 
            file_path
        ], capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except:
        return 0.0

def trim_silence_ffmpeg(input_path: str, output_path: str):
    """
    Uses FFmpeg to trim silence from both start and end of an audio file.
    This replaces the pydub dependency to avoid audioop issues in Python 3.13+.
    """
    try:
        # silenceremove filter: 
        # start_periods=1: remove leading silence
        # areverse + start_periods=1 + areverse: remove trailing silence
        subprocess.run([
            'ffmpeg', '-i', input_path, 
            '-af', 'silenceremove=start_periods=1:start_threshold=-50dB,areverse,silenceremove=start_periods=1:start_threshold=-50dB,areverse',
            '-y', output_path
        ], check=True, capture_output=True)
        return True
    except Exception as e:
        print(f"❌ FFmpeg trimming error: {e}")
        return False

async def process_audio(reciter_name: str, surah: int, start_ayah: int, end_ayah: int) -> tuple[str, float, list]:
    """
    Downloads ayah audio files, trims silence with FFmpeg, and merges them.
    Uses system temporary directory for all operations.
    Returns: path, total duration, and timings.
    """
    import tempfile
    import uuid
    batch_id = str(uuid.uuid4())[:8]
    reciter_id = RECITER_MAP.get(reciter_name, "Alafasy_128kbps")
    base_url = f"https://everyayah.com/data/{reciter_id}"
    surah_str = f"{surah:03d}"
    
    # Create a persistent temp dir for this batch that will be cleaned up eventually by OS
    temp_dir = tempfile.gettempdir()
    audio_temp_dir = os.path.join(temp_dir, "quran_video_audio", reciter_id)
    trimmed_temp_dir = os.path.join(audio_temp_dir, "trimmed")
    os.makedirs(audio_temp_dir, exist_ok=True)
    os.makedirs(trimmed_temp_dir, exist_ok=True)
    
    trimmed_files = []
    ayah_timings = []
    current_time = 0.0
    
    # تجهيز قائمة الآيات للتحميل
    ayahs_to_process = []
    
    # إضافة الآيات المطلوبة مباشرة بدون البسملة التلقائية
    for a in range(start_ayah, end_ayah + 1):
        ayahs_to_process.append({'s': surah, 'a': a, 'is_basmala': False})

    for item in ayahs_to_process:
        s_str = f"{item['s']:03d}"
        a_str = f"{item['a']:03d}"
        file_name = f"{surah_str}{a_str}.mp3"
        
        local_file_name = f"{surah_str}{a_str}.mp3"
        
        url = f"{base_url}/{file_name}"
        local_path = os.path.join(audio_temp_dir, local_file_name)
        trimmed_path = os.path.join(trimmed_temp_dir, local_file_name)
        
        # 1. Download
        if not os.path.exists(local_path):
            print(f"Downloading {url}...")
            try:
                response = requests.get(url, headers=HEADERS, timeout=15)
                if response.status_code == 200:
                    with open(local_path, "wb") as f:
                        f.write(response.content)
                else:
                    print(f"❌ Failed to download {url}")
                    continue
            except Exception as e:
                print(f"❌ Error downloading {url}: {e}")
                continue
        
        # 2. Trim Silence using FFmpeg (No pydub needed)
        if os.path.exists(local_path):
            if trim_silence_ffmpeg(local_path, trimmed_path):
                duration = get_audio_duration(trimmed_path)
                if duration > 0:
                    trimmed_files.append(trimmed_path)
                    ayah_timings.append({
                        'ayah_num': 0 if item.get('is_basmala') else item['a'],
                        'start': current_time,
                        'end': current_time + duration
                    })
                    current_time += duration
            else:
                # If trimming fails, use original
                duration = get_audio_duration(local_path)
                trimmed_files.append(local_path)
                ayah_timings.append({
                    'ayah_num': 0 if item.get('is_basmala') else item['a'],
                    'start': current_time,
                    'end': current_time + duration
                })
                current_time += duration
    
    if not trimmed_files:
        raise Exception("No audio files were processed successfully")
    
    # 3. Concatenate using FFmpeg
    output_path = os.path.join(audio_temp_dir, f"merged_{surah}_{start_ayah}-{end_ayah}_{reciter_id}_{batch_id}.mp3")
    concat_file = os.path.join(audio_temp_dir, f"concat_{batch_id}.txt")
    
    with open(concat_file, 'w', encoding='utf-8') as f:
        for audio_file in trimmed_files:
            abs_path = os.path.abspath(audio_file).replace('\\', '/')
            f.write(f"file '{abs_path}'\n")
    
    try:
        subprocess.run([
            'ffmpeg', '-f', 'concat', '-safe', '0', 
            '-i', concat_file, '-c:a', 'libmp3lame', '-q:a', '2', '-y', output_path
        ], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"FFmpeg concat error: {e.stderr.decode()}")
        raise Exception("Failed to merge audio files with ffmpeg")
    
    try:
        os.remove(concat_file)
    except:
        pass
    
    return output_path, current_time, ayah_timings

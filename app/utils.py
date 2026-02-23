import httpx
import asyncio
import os
import re

async def fetch_arabic_text_list(surah: int, start_ayah: int, end_ayah: int, edition: str = "quran-uthmani", fetch_translation: bool = False) -> list:
    """
    جلب النصوص من AlQuran.cloud
    """
    try:
        # إذا اختار المستخدم نسخة نصية من AlQuran.cloud (تبدأ بـ quran-) مثل quran-tajweed نلتزم بها
        # أما إذا اختار "خط المصحف الشريف" أو "الرسم العثماني" نستخدم quran-uthmani لجلب الحركات
        if edition.startswith("quran-"):
            source_edition = edition
        elif "Mushaf" in edition or "Uthmanic" in edition or "HAFS" in edition:
            source_edition = "quran-uthmani"
        else:
            source_edition = "quran-simple"
        
        ayahs_data = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            for ayah_num in range(start_ayah, end_ayah + 1):
                if ayah_num == 0:
                    # توفير نص البسملة يدوياً لأن AlQuran.cloud لا يدعم الآية رقم 0 (البسملة المستقلة)
                    basmala_text = "بِسْمِ ٱللَّهِ ٱلرَّحْمَـٰنِ ٱلرَّحِيمِ"
                    basmala_trans = "In the name of Allah, the Entirely Merciful, the Especially Merciful." if fetch_translation else ""
                    
                    ayahs_data.append({
                        'ayah_num': 0,
                        'words': basmala_text.split(),
                        'translation': basmala_trans
                    })
                    continue
                
                # محاولة الجلب مع نظام إعادة المحاولة في حالة الفشل (مثل خطأ 429)
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        # 1. جلب النص العربي
                        text_url = f"https://api.alquran.cloud/v1/ayah/{surah}:{ayah_num}/{source_edition}"
                        text_res = await client.get(text_url)
                        
                        if text_res.status_code == 200:
                            text_data = text_res.json()['data']
                            text = text_data['text']
                            
                            translation_text = ""
                            if fetch_translation:
                                # 2. جلب الترجمة الإنجليزية
                                trans_url = f"https://api.alquran.cloud/v1/ayah/{surah}:{ayah_num}/en.sahih"
                                trans_res = await client.get(trans_url)
                                if trans_res.status_code == 200:
                                    translation_text = trans_res.json()['data']['text']

                            # حذف نص البسملة من الآية الأولى (باستثناء الفاتحة)
                            if ayah_num == 1 and surah != 1:
                                text = re.sub(r'<[^>]+>', '', text)
                                words_temp = text.split()
                                if len(words_temp) >= 4:
                                    # التمييز بين البسملة والنص الأصلي
                                    def clean_chars(t): return "".join(c for c in t if '\u0621' <= c <= '\u064A' or c == '\u0671')
                                    if "بِسْمِ" in text or "بسم" in clean_chars(words_temp[0]):
                                        # تخطي أول 4 كلمات (بِسْمِ ٱللَّهِ ٱلرَّحْمَـٰنِ ٱلرَّحِيمِ)
                                        # نستخدم split/join لضمان تنظيف الفراغات
                                        text = " ".join(words_temp[4:]).strip() if len(words_temp) > 4 else text
                            
                            # تنظيف الوسوم
                            text = re.sub(r'<[^>]+>', '', text)
                            word_list = text.split()
                            
                            if word_list:
                                word_list = [re.sub(r'<[^>]+>', '', w) for w in word_list]
                                # إضافة رقم الآية - نستخدم الأقواس المزخرفة الأصلية
                                word_list.append(f"﴿{ayah_num}﴾")
                                    
                                ayahs_data.append({
                                    'ayah_num': ayah_num,
                                    'words': word_list,
                                    'translation': translation_text
                                })
                            # نجح الجلب، اخرج من حلقة المحاولات
                            break
                        elif text_res.status_code == 429:
                            # إذا تم الحظر، انتظر قليلاً ثم أعد المحاولة
                            print(f"⚠️ Rate limit hit for ayah {ayah_num}, retrying in 2 seconds...")
                            if asyncio.get_event_loop().is_running():
                                await asyncio.sleep(2)
                            else:
                                import time
                                time.sleep(2)
                        else:
                            print(f"❌ Failed to fetch ayah {ayah_num}: {text_res.status_code}")
                            break
                    except Exception as e:
                        print(f"⚠️ Attempt {attempt+1} failed for ayah {ayah_num}: {e}")
                        if asyncio.get_event_loop().is_running():
                            await asyncio.sleep(1)
                        else:
                            import time
                            time.sleep(1)
                
                # تأخير بسيط جداً بين الآيات لتجنب الحظر
                if asyncio.get_event_loop().is_running():
                    await asyncio.sleep(0.3)
                else:
                    import time
                    time.sleep(0.3)
        
        return ayahs_data
    except Exception as e:
        print(f"Error fetching Quran data: {e}")
        return []

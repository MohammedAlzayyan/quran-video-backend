import os
import requests
from dotenv import load_dotenv

load_dotenv()

# Supabase configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME", "videos")

def upload_video_to_supabase(file_path: str, destination_path: str) -> str:
    """
    Uploads a video to Supabase Storage using direct HTTP requests.
    This avoids heavy dependencies like the supabase-py library.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("⚠️ Supabase credentials missing (.env). Skipping cloud upload.")
        return None

    # Clean the URL (remove trailing slash if exists)
    base_url = SUPABASE_URL.rstrip('/')
    
    # Supabase Storage Upload API Endpoint
    # Format: https://[project-id].supabase.co/storage/v1/object/[bucket]/[path]
    upload_url = f"{base_url}/storage/v1/object/{BUCKET_NAME}/{destination_path}"
    
    headers = {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "video/mp4"
    }

    try:
        print(f"☁️ Uploading to Supabase: {destination_path}...")
        with open(file_path, 'rb') as f:
            # We use x-upsert header to overwrite if exists, though filenames are usually unique
            response = requests.post(upload_url, headers=headers, data=f)
            
        if response.status_code == 200:
            # Construct the public URL
            # Format: https://[project-id].supabase.co/storage/v1/object/public/[bucket]/[path]
            public_url = f"{base_url}/storage/v1/object/public/{BUCKET_NAME}/{destination_path}"
            return public_url
        else:
            print(f"❌ Supabase Upload Error ({response.status_code}): {response.text}")
            return None
            
    except Exception as e:
        print(f"❌ Unexpected error during Supabase upload: {e}")
        return None

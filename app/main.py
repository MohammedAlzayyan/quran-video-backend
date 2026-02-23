from dotenv import load_dotenv
import os

# Load environment variables at the very beginning
load_dotenv()

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.api import router
from app.auth import router as auth_router
from app.database import engine
from app import models

# Create database tables
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Quran Video Generator API")

# Ensure fonts are ready on startup (crucial for Hugging Face)
try:
    from app.video_generator import ensure_fonts_downloaded
    ensure_fonts_downloaded()
except Exception as e:
    print(f"⚠️ Startup font check failed: {e}")

# Configure CORS - allow all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure storage directory exists for persistent video storage
STORAGE_DIR = "storage"
if not os.path.exists(STORAGE_DIR):
    os.makedirs(STORAGE_DIR)
    os.makedirs(os.path.join(STORAGE_DIR, "videos"))

app.mount("/static", StaticFiles(directory=STORAGE_DIR), name="static")

app.include_router(router, prefix="/api")
app.include_router(auth_router, prefix="/api/auth")

@app.get("/")
def read_root():
    return {"message": "Quran Video Generator API is running"}

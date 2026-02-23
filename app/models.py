from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    country = Column(String)
    image = Column(String, nullable=True) # Base64 or URL
    is_verified = Column(Boolean, default=False)
    verification_code = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    videos = relationship("VideoJob", back_populates="user")

class VideoJob(Base):
    __tablename__ = "video_jobs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    surah_name = Column(String)
    reciter_name = Column(String)
    ayah_range = Column(String)
    status = Column(String, default="completed") # processing, completed, failed
    progress_message = Column(String, nullable=True) # Real-time status update
    video_path = Column(String, nullable=True) # المسار الدائم للملف على السيرفر
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="videos")

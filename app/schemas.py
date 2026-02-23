from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime

class UserBase(BaseModel):
    email: EmailStr
    name: str
    country: str
    image: Optional[str] = None

class UserCreate(UserBase):
    password: str

class UserUpdate(BaseModel):
    name: Optional[str] = None
    country: Optional[str] = None
    image: Optional[str] = None

class ChangePassword(BaseModel):
    current_password: str
    new_password: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserVerify(BaseModel):
    email: EmailStr
    code: str

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class UserResetPassword(BaseModel):
    email: EmailStr
    code: str
    new_password: str

class User(UserBase):
    id: int
    is_verified: bool
    created_at: datetime

    class Config:
        from_attributes = True

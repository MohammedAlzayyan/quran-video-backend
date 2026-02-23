from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session
import random
from datetime import datetime

from .database import get_db
from . import models, schemas, auth_utils, dependencies

router = APIRouter(tags=["Authentication"])

def mock_send_email(email: str, code: str):
    """
    Simulation of sending an email. In production, connect this to SendGrid/Gmail.
    """
    print(f"\n--- 📧 EMAIL SYSTEM ---")
    print(f"To: {email}")
    print(f"Subject: Your Verification Code")
    print(f"Body: Welcome! Your verification code is: {code}")
    print(f"-----------------------\n")

@router.post("/register", response_model=schemas.User)
def register(user: schemas.UserCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    # Check if user already exists
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if db_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="البريد الإلكتروني مسجل مسبقاً"
        )
    
    # Generate 5-digit verification code
    v_code = str(random.randint(10000, 99999))
    
    # Create new user
    new_user = models.User(
        email=user.email,
        name=user.name,
        country=user.country,
        hashed_password=auth_utils.hide_password(user.password),
        verification_code=v_code,
        is_verified=False
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    # Send verification email in background
    background_tasks.add_task(mock_send_email, user.email, v_code)
    
    return new_user

@router.post("/verify")
def verify_code(data: schemas.UserVerify, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.email == data.email).first()
    
    if not db_user:
        raise HTTPException(status_code=404, detail="المستخدم غير موجود")
    
    if db_user.verification_code == data.code:
        db_user.is_verified = True
        db_user.verification_code = None  # Clear code after verification
        db.commit()
        return {"message": "تم التحقق من الحساب بنجاح"}
    
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="كود التحقق غير صحيح")

@router.post("/login")
def login(data: schemas.UserLogin, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.email == data.email).first()
    
    if not db_user or not auth_utils.verify_password(data.password, db_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="البريد الإلكتروني أو كلمة المرور غير صحيحة"
        )
    
    if not db_user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="يرجى التحقق من بريدك الإلكتروني أولاً"
        )
    
    access_token = auth_utils.create_access_token(data={"sub": db_user.email})
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "name": db_user.name,
            "email": db_user.email,
            "country": db_user.country
        }
    }

@router.get("/me", response_model=schemas.User)
def get_me(current_user: models.User = Depends(dependencies.get_current_user)):
    return current_user

@router.put("/update-profile", response_model=schemas.User)
def update_profile(data: schemas.UserUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(dependencies.get_current_user)):
    if data.name is not None:
        current_user.name = data.name
    if data.country is not None:
        current_user.country = data.country
    if data.image is not None:
        current_user.image = data.image
    
    db.commit()
    db.refresh(current_user)
    return current_user

@router.post("/change-password")
def change_password(data: schemas.ChangePassword, db: Session = Depends(get_db), current_user: models.User = Depends(dependencies.get_current_user)):
    # Verify current password
    if not auth_utils.verify_password(data.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="كلمة المرور الحالية غير صحيحة"
        )
    
    # Hash and save new password
    current_user.hashed_password = auth_utils.hide_password(data.new_password)
    db.commit()
    
    return {"message": "تم تغيير كلمة المرور بنجاح"}

@router.post("/resend-code")
def resend_code(email: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.email == email).first()
    
    if not db_user:
        raise HTTPException(status_code=404, detail="المستخدم غير موجود")
    
    new_code = str(random.randint(10000, 99999))
    db_user.verification_code = new_code
    db.commit()
    
    background_tasks.add_task(mock_send_email, email, new_code)
    
    return {"message": "تم إعادة إرسال كود التحقق الجديد"}

@router.post("/forgot-password")
def forgot_password(data: schemas.ForgotPasswordRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.email == data.email).first()
    
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="عذراً، هذا البريد الإلكتروني غير مسجل في نظامنا"
        )
    
    reset_code = str(random.randint(10000, 99999))
    db_user.verification_code = reset_code
    db.commit()
    
    background_tasks.add_task(mock_send_email, data.email, reset_code)
    
    return {"message": "تم إرسال كود استعادة كلمة المرور إلى بريدك الإلكتروني"}

@router.post("/reset-password")
def reset_password(data: schemas.UserResetPassword, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.email == data.email).first()
    
    if not db_user:
        raise HTTPException(status_code=404, detail="المستخدم غير موجود")
    
    if db_user.verification_code != data.code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="كود التحقق غير صحيح")
    
    # Update password and clear code
    db_user.hashed_password = auth_utils.hide_password(data.new_password)
    db_user.verification_code = None
    db.commit()
    
    return {"message": "تم إعادة تعيين كلمة المرور بنجاح، يمكنك الآن تسجيل الدخول"}

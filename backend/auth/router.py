from backend.database.models import UserDB
from backend.database.session import get_db
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from .schemas import TokenResponse, UserLoginRequest
from .security import create_access_token, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: UserLoginRequest, db: AsyncSession = Depends(get_db)
):
  """Авторизация пользователя и выдача JWT-токена."""
  result = await db.execute(
      select(UserDB).where(UserDB.login == form_data.login)
  )
  user = result.scalars().first()

  if not user or not verify_password(form_data.password, user.password_hash):
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Неверный логин или пароль",
        headers={"WWW-Authenticate": "Bearer"},
    )

  access_token = create_access_token(data={"sub": str(user.id), "role": user.role})
  return {"access_token": access_token, "token_type": "bearer"}
from datetime import datetime, timedelta
import jwt
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

SECRET_KEY = "super-secret-key-change-me-in-production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # Токен жив 24 часа


def verify_password(plain_password: str, hashed_password: str) -> bool:
  """Проверка соответствия пароля его хэшу."""
  return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
  """Генерация хэша пароля с использованием Argon2id."""
  return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
  """Создание JWT-токена доступа."""
  to_encode = data.copy()
  expire = datetime.utcnow() + (
      expires_delta
      if expires_delta
      else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
  )
  to_encode.update({"exp": expire})
  return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
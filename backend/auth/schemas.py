from pydantic import BaseModel, Field


class UserLoginRequest(BaseModel):
  login: str = Field(..., description="Логин сотрудника")
  password: str = Field(..., description="Пароль")


class TokenResponse(BaseModel):
  access_token: str
  token_type: str = "bearer"


class UserResponse(BaseModel):
  id: str
  login: str
  role: str

  class Config:
    from_attributes = True
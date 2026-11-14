import uuid
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from backend.database.session import Base


class UserDB(Base):
  __tablename__ = "users"

  id = Column(
      UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True
  )
  login = Column(String, unique=True, index=True, nullable=False)
  password_hash = Column(String, nullable=False)
  role = Column(String, default="user", nullable=False)
  is_active = Column(Integer, default=1, nullable=False)
  created_at = Column(DateTime(timezone=True), server_default=func.now())


class ConversationDB(Base):
  __tablename__ = "conversations"

  id = Column(
      UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True
  )
  owner_user_id = Column(
      UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
  )
  title = Column(String, default="Новый диалог")
  created_at = Column(DateTime(timezone=True), server_default=func.now())
  updated_at = Column(
      DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
  )


class MessageDB(Base):
  __tablename__ = "messages"

  id = Column(
      UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True
  )
  conversation_id = Column(
      UUID(as_uuid=True),
      ForeignKey("conversations.id", ondelete="CASCADE"),
      nullable=False,
      index=True,
  )
  owner_user_id = Column(
      UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
  )
  role = Column(String, nullable=False)
  content = Column(Text, nullable=False)
  client_message_id = Column(UUID(as_uuid=True), nullable=True)
  created_at = Column(DateTime(timezone=True), server_default=func.now())


class JobDB(Base):
  __tablename__ = "jobs"

  id = Column(
      UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True
  )
  job_type = Column(String, nullable=False)
  status = Column(String, default="pending", nullable=False)
  owner_user_id = Column(
      UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
  )
  idempotency_key = Column(String, nullable=True)
  payload = Column(JSONB, nullable=False)
  attempt = Column(Integer, default=0)
  created_at = Column(DateTime(timezone=True), server_default=func.now())
  updated_at = Column(
      DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
  )
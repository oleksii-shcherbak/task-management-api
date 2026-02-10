from enum import Enum as PyEnum

from sqlalchemy import Boolean, Column, DateTime, Enum, Integer, String, func

from app.database import Base


class UserRole(PyEnum):
    MEMBER = "member"
    MANAGER = "manager"
    ADMIN = "admin"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    name = Column(String, nullable=False)
    role = Column(Enum(UserRole), default=UserRole.MEMBER, nullable=False)
    is_active = Column(
        Boolean, default=False, nullable=False
    )  # New users start inactive
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self):
        return f"<User(id={self.id}, email='{self.email}')>"  # Simple string representation for debugging

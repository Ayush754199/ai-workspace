from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class User(Base):
    __tablename__ = "users"
    # Keep instances usable after the session that loaded them is closed; several
    # fixtures and test helpers read user attributes post-commit.
    __mapper_args__ = {"eager_defaults": True}

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=True)
    mobile_number = Column(String(32), unique=True, index=True, nullable=True)
    identifier = Column(String, unique=True, index=True, nullable=True)
    mongo_id = Column(String, unique=True, index=True, nullable=True)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    dob = Column(String, nullable=True)
    role = Column(String, default="user")
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, nullable=False, default=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    feature_permissions = relationship(
        "UserFeaturePermission",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class UserFeaturePermission(Base):
    __tablename__ = "user_feature_permissions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    feature = Column(String(100), nullable=False, index=True)
    enabled = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="feature_permissions")

    __table_args__ = (
        UniqueConstraint("user_id", "feature", name="uq_user_feature_permission"),
    )

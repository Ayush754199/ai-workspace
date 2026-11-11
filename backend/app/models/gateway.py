from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.sql import func
from app.core.database import Base


class Provider(Base):
    __tablename__ = "providers"
    id = Column(Integer, primary_key=True)
    name = Column(String(80), unique=True, nullable=False, index=True)
    enabled = Column(Boolean, nullable=False, default=False)
    free_only = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ProviderModel(Base):
    __tablename__ = "provider_models"
    id = Column(Integer, primary_key=True)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=False, index=True)
    model = Column(String(160), nullable=False)
    is_free = Column(Boolean, nullable=False, default=True)
    capabilities = Column(JSON, nullable=False, default=list)
    enabled = Column(Boolean, nullable=False, default=False)
    availability = Column(String(40), nullable=False, default="unknown")
    verified_at = Column(DateTime(timezone=True), nullable=True)
    max_input_tokens = Column(Integer, nullable=True)
    max_output_tokens = Column(Integer, nullable=True)
    restrictions = Column(JSON, nullable=False, default=list)
    __table_args__ = (UniqueConstraint("provider_id", "model", name="uq_provider_model"),)


class UserQuota(Base):
    __tablename__ = "user_quotas"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    daily_limit = Column(Integer, nullable=False, default=0)
    daily_used = Column(Integer, nullable=False, default=0)
    reserved = Column(Integer, nullable=False, default=0)
    per_chat_limit = Column(Integer, nullable=False, default=20)
    daily_window = Column(Date, nullable=False, server_default=func.current_date())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CapabilityQuota(Base):
    """Independent daily quota for image/video (and future AI capabilities)."""
    __tablename__ = "capability_quotas"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    capability = Column(String(40), nullable=False)
    daily_limit = Column(Integer, nullable=False, default=0)
    daily_used = Column(Integer, nullable=False, default=0)
    reserved = Column(Integer, nullable=False, default=0)
    daily_window = Column(Date, nullable=False, server_default=func.current_date())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("user_id", "capability", name="uq_user_capability_quota"),)


class ImageUsage(Base):
    __tablename__ = "image_usages"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    conversation_id = Column(Integer, ForeignKey("ai_conversations.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class ImagePremiumRequest(Base):
    __tablename__ = "image_premium_requests"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="pending")
    daily_limit = Column(Integer, nullable=False, default=20)
    requested_at = Column(DateTime(timezone=True), server_default=func.now())
    reviewed_at = Column(DateTime(timezone=True), nullable=True)


class ChatQuota(Base):
    __tablename__ = "chat_quotas"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    chat_id = Column(String(200), nullable=False)
    used = Column(Integer, nullable=False, default=0)
    reserved = Column(Integer, nullable=False, default=0)
    daily_window = Column(Date, nullable=False, server_default=func.current_date())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("user_id", "chat_id", name="uq_user_chat_quota"),)


class QuotaReservation(Base):
    __tablename__ = "quota_reservations"
    id = Column(Integer, primary_key=True)
    reservation_id = Column(String(80), unique=True, nullable=False, index=True)
    request_id = Column(String(160), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    chat_id = Column(String(200), nullable=True, index=True)
    capability = Column(String(40), nullable=False, default="chat", index=True)
    provider = Column(String(80), nullable=False)
    model = Column(String(160), nullable=False)
    units = Column(Integer, nullable=False, default=1)
    state = Column(String(20), nullable=False, default="RESERVED", index=True)
    uncertain = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)


class UsageLog(Base):
    __tablename__ = "usage_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    reservation_id = Column(String(80), nullable=True, index=True)
    request_id = Column(String(160), unique=True, nullable=False, index=True)
    provider = Column(String(80), nullable=False)
    model = Column(String(160), nullable=False)
    mode = Column(String(40), nullable=False, default="chat")
    status = Column(String(40), nullable=False)
    units = Column(Integer, nullable=False, default=1)
    detail = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ProviderHealth(Base):
    __tablename__ = "provider_health"
    id = Column(Integer, primary_key=True)
    provider = Column(String(80), unique=True, nullable=False)
    status = Column(String(40), nullable=False, default="unknown")
    cooldown_until = Column(DateTime(timezone=True), nullable=True)
    detail = Column(Text, nullable=True)
    checked_at = Column(DateTime(timezone=True), server_default=func.now())


class AdminSetting(Base):
    __tablename__ = "admin_settings"
    id = Column(Integer, primary_key=True)
    key = Column(String(120), unique=True, nullable=False)
    value = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProviderBudget(Base):
    """Atomic server-side budget bucket for provider spend or account units."""
    __tablename__ = "provider_budgets"
    id = Column(Integer, primary_key=True)
    provider = Column(String(80), nullable=False)
    bucket = Column(String(80), nullable=False)
    period = Column(String(20), nullable=False)  # monthly | daily
    window_key = Column(String(40), nullable=False)
    limit_units = Column(Float, nullable=False, default=0)
    used_units = Column(Float, nullable=False, default=0)
    reserved_units = Column(Float, nullable=False, default=0)
    unit_name = Column(String(40), nullable=False, default="units")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("provider", "bucket", "period", name="uq_provider_budget_bucket"),)


class ProviderBudgetReservation(Base):
    """Idempotent reservation so concurrent requests cannot overspend a bucket."""
    __tablename__ = "provider_budget_reservations"
    id = Column(Integer, primary_key=True)
    request_id = Column(String(160), unique=True, nullable=False, index=True)
    budget_id = Column(Integer, ForeignKey("provider_budgets.id"), nullable=False, index=True)
    units = Column(Float, nullable=False)
    state = Column(String(20), nullable=False, default="RESERVED", index=True)
    detail = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CreditTransaction(Base):
    __tablename__ = "credit_transactions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    reason = Column(String(160), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class MediaJob(Base):
    __tablename__ = "media_jobs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(String(40), nullable=False)
    status = Column(String(40), nullable=False, default="queued")
    progress = Column(Integer, nullable=False, default=0)
    payload = Column(JSON, nullable=False, default=dict)
    output_uri = Column(String(500), nullable=True)
    error = Column(Text, nullable=True)
    provider = Column(String(80), nullable=True)
    model = Column(String(160), nullable=True)
    request_id = Column(String(160), nullable=True, index=True)
    reservation_id = Column(String(80), nullable=True, index=True)
    # Recovery bookkeeping: which process holds the job and when it last pinged.
    worker_id = Column(String(80), nullable=True, index=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

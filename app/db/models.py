"""SQLAlchemy ORM models for the application."""

from datetime import datetime, date
from sqlalchemy import Column, Integer, String, Boolean, Text, Date, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.db.base import Base


class Workspace(Base):
    """Slack workspace configuration."""

    __tablename__ = "workspaces"

    id = Column(Integer, primary_key=True)
    slack_team_id = Column(String(255), unique=True, nullable=False)
    report_channel_id = Column(String(255), nullable=False)
    default_time = Column(String(10), nullable=False, default="09:30")
    timezone = Column(String(50), nullable=False, default="Asia/Kolkata")
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    users = relationship("User", back_populates="workspace", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Workspace(id={self.id}, slack_team_id={self.slack_team_id})>"


class User(Base):
    """Slack user."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    slack_user_id = Column(String(255), unique=True, nullable=False)
    display_name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True)
    timezone = Column(String(50), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    workspace = relationship("Workspace", back_populates="users")
    reports = relationship("StandupReport", back_populates="user", cascade="all, delete-orphan")
    state = relationship("StandupState", back_populates="user", cascade="all, delete-orphan", uselist=False)

    def __repr__(self) -> str:
        return f"<User(id={self.id}, slack_user_id={self.slack_user_id}, display_name={self.display_name})>"


class StandupReport(Base):
    """Daily standup report from a user."""

    __tablename__ = "standup_reports"
    __table_args__ = (UniqueConstraint("user_id", "report_date", name="uq_user_date"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    report_date = Column(Date, nullable=False)
    feeling = Column(Text, nullable=True)
    yesterday = Column(Text, nullable=True)
    today = Column(Text, nullable=True)
    blockers = Column(Text, nullable=True)
    skipped = Column(Boolean, nullable=False, default=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="reports")

    def __repr__(self) -> str:
        return f"<StandupReport(id={self.id}, user_id={self.user_id}, report_date={self.report_date})>"


class StandupState(Base):
    """Current standup state for a user (pending report tracking)."""

    __tablename__ = "standup_states"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_state"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    pending_report_date = Column(Date, nullable=False)
    current_question_index = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="state")

    def __repr__(self) -> str:
        return f"<StandupState(user_id={self.user_id}, question_index={self.current_question_index})>"

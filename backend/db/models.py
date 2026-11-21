"""ORM models for the persistence layer.

Ids are string UUIDs and datetimes are naive UTC so SQLite and Postgres behave alike.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    return str(uuid.uuid4())


def _fk(target: str) -> Mapped[str]:
    return mapped_column(String(36), ForeignKey(target, ondelete="CASCADE"), index=True)


class Workspace(Base):
    __tablename__ = "workspace"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)

    agents: Mapped[list["Agent"]] = relationship(back_populates="workspace", cascade="all, delete-orphan")
    events: Mapped[list["Event"]] = relationship(back_populates="workspace", cascade="all, delete-orphan")


class Agent(Base):
    __tablename__ = "agent"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = _fk("workspace.id")
    name: Mapped[str] = mapped_column(String(255))
    user_role_label: Mapped[str] = mapped_column(String(255))
    # Deliberately not a foreign key: avoids a circular dependency with prompt_version.
    current_prompt_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_checked_at_run: Mapped[int] = mapped_column(Integer, default=0)
    pattern_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    workspace: Mapped[Workspace] = relationship(back_populates="agents")
    prompt_versions: Mapped[list["PromptVersion"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    behavior_maps: Mapped[list["BehaviorMap"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    runs: Mapped[list["Run"]] = relationship(back_populates="agent", cascade="all, delete-orphan")
    criteria: Mapped[list["Criterion"]] = relationship(back_populates="agent", cascade="all, delete-orphan")


class PromptVersion(Base):
    __tablename__ = "prompt_version"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agent_id: Mapped[str] = _fk("agent.id")
    text: Mapped[str] = mapped_column(Text)
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    origin: Mapped[str] = mapped_column(String(32))  # Phase 1: "initial"
    status: Mapped[str] = mapped_column(String(32))  # Phase 1: "promoted"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    agent: Mapped[Agent] = relationship(back_populates="prompt_versions")


class BehaviorMap(Base):
    __tablename__ = "behavior_map"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agent_id: Mapped[str] = _fk("agent.id")
    prompt_hash: Mapped[str] = mapped_column(String(64))
    analysis: Mapped[dict] = mapped_column(JSON)

    agent: Mapped[Agent] = relationship(back_populates="behavior_maps")


class Run(Base):
    __tablename__ = "run"
    __table_args__ = (UniqueConstraint("agent_id", "position", name="uq_run_agent_position"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agent_id: Mapped[str] = _fk("agent.id")
    prompt_version_id: Mapped[str] = _fk("prompt_version.id")
    position: Mapped[int] = mapped_column(Integer)  # 1-based per agent
    messages: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    agent: Mapped[Agent] = relationship(back_populates="runs")


class Criterion(Base):
    __tablename__ = "criterion"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agent_id: Mapped[str] = _fk("agent.id")
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    definition: Mapped[str] = mapped_column(Text)
    good_example: Mapped[str] = mapped_column(Text)
    bad_example: Mapped[str] = mapped_column(Text)
    why_discovered: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    agent: Mapped[Agent] = relationship(back_populates="criteria")


class Event(Base):
    __tablename__ = "event"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = _fk("workspace.id")
    name: Mapped[str] = mapped_column(String(255))
    properties: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    workspace: Mapped[Workspace] = relationship(back_populates="events")

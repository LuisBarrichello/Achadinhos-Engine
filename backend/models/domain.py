import re
import time
from typing import Optional

from pydantic import BaseModel, HttpUrl, field_validator
from sqlmodel import Field, SQLModel

from services.matching import _normalize_keyword


# ── Tabelas ───────────────────────────────────────────────────────────────────

class Link(SQLModel, table=True):
    id          : Optional[int] = Field(default=None, primary_key=True)
    title       : str           = Field(index=True)
    url         : str
    emoji       : str           = "🛍️"
    badge       : Optional[str] = None
    badge_color : str           = "#e11d48"
    active      : bool          = True
    order       : int           = 0
    clicks      : int           = 0
    image_url   : Optional[str] = None
    keyword     : Optional[str] = Field(default=None, index=True)


class KeywordLink(SQLModel, table=True):
    id      : Optional[int] = Field(default=None, primary_key=True)
    keyword : str           = Field(index=True)
    url     : str
    message : str           = "Oi! Aqui está seu link 👇\n{url}"


class WebhookEvent(SQLModel, table=True):
    __tablename__ = "webhook_events"
    id           : Optional[int] = Field(default=None, primary_key=True)
    user_id      : str
    message      : str
    status       : str           = Field(default="pending", index=True)
    created_at   : int           = Field(default_factory=lambda: int(time.time()))
    processed_at : Optional[int] = None
    error        : Optional[str] = None


# ── Schemas Pydantic ──────────────────────────────────────────────────────────

class LinkCreate(BaseModel):
    title       : str
    url         : HttpUrl
    emoji       : str           = "🛍️"
    badge       : Optional[str] = None
    badge_color : str           = "#e11d48"
    active      : bool          = True
    order       : int           = 0
    image_url   : Optional[str] = None
    keyword     : Optional[str] = None

    @field_validator("badge_color")
    @classmethod
    def validate_hex_color(cls, v: str) -> str:
        if not re.match(r'^#[0-9A-Fa-f]{6}$', v):
            raise ValueError("badge_color deve ser um hex RGB válido, ex: #e11d48")
        return v

    @field_validator("keyword")
    @classmethod
    def normalize_keyword(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        return _normalize_keyword(v) or None

    @field_validator("image_url")
    @classmethod
    def validate_image_url(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        v = v.strip()
        if not re.match(r'^https?://', v):
            raise ValueError("image_url deve começar com http:// ou https://")
        return v


class LinkRead(BaseModel):
    id          : int
    title       : str
    url         : str
    emoji       : str
    badge       : Optional[str]
    badge_color : str
    active      : bool
    order       : int
    clicks      : int
    image_url   : Optional[str]
    keyword     : Optional[str]

    class Config:
        from_attributes = True


class WebhookEventRead(BaseModel):
    id         : int
    user_id    : str
    message    : str
    status     : str
    created_at : int

    class Config:
        from_attributes = True


class EventStatusUpdate(BaseModel):
    status : str                # pending | processing | completed | failed
    error  : Optional[str] = None


class SystemStatus(BaseModel):
    db_connected    : bool
    webhook_active  : bool
    dm_count_today  : int
    pending_dms     : int
    version         : str
    image_mode      : str

import logging
from contextlib import asynccontextmanager

import sqlalchemy
from sqlmodel import Session, SQLModel, create_engine, select

from core.config import DATABASE_URL
from models.domain import KeywordLink, Link, WebhookEvent  # noqa: F401 — necessário para metadata

log = logging.getLogger("achadinhos")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_recycle=300,
)


def get_session():
    with Session(engine) as session:
        yield session


def _run_migrations() -> None:
    migrations = [
        "ALTER TABLE link ADD COLUMN IF NOT EXISTS image_url TEXT",
        "ALTER TABLE link ADD COLUMN IF NOT EXISTS keyword TEXT",
        """
        CREATE TABLE IF NOT EXISTS webhook_events (
            id           SERIAL PRIMARY KEY,
            user_id      TEXT    NOT NULL,
            message      TEXT    NOT NULL,
            status       TEXT    NOT NULL DEFAULT 'pending',
            created_at   INTEGER NOT NULL,
            processed_at INTEGER,
            error        TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_webhook_events_status ON webhook_events (status)",
    ]
    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(sqlalchemy.text(sql))
                conn.commit()
                log.info("✅ Migration OK")
            except Exception as exc:
                log.debug(f"Migration ignorada ({exc})")


@asynccontextmanager
async def lifespan(app):  # type: ignore[type-arg]
    _run_migrations()
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        if not session.exec(select(Link)).first():
            seed_links = [
                Link(
                    title="🔥 Ofertas Shopee do Dia",
                    url="https://shopee.com.br/seu_link_afiliado",
                    emoji="🔥", badge="OFERTA", order=0, keyword="SHOPEE",
                ),
                Link(
                    title="⚡ Mercado Livre em Destaque",
                    url="https://mercadolivre.com.br/seu_link",
                    emoji="⚡", badge="TOP", order=1, keyword="MELI",
                ),
            ]
            for lk in seed_links:
                session.add(lk)
            session.commit()

        if not session.exec(select(KeywordLink)).first():
            session.add(KeywordLink(
                keyword="EU QUERO",
                url="https://shopee.com.br/seu_link_afiliado"
            ))
            session.commit()

    log.info("✅ Banco PostgreSQL inicializado.")
    yield

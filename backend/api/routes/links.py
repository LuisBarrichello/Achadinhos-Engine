from typing import List

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from api.dependencies import _rate_limit_click, verify_admin
from core.database import get_session
from models.domain import Link, LinkCreate, LinkRead

router = APIRouter(tags=["links"])


@router.get("/links", response_model=List[LinkRead])
def list_links(session: Session = Depends(get_session)):
    return session.exec(
        select(Link).where(Link.active == True).order_by(Link.order)
    ).all()


@router.post("/links", response_model=LinkRead, dependencies=[Depends(verify_admin)])
def create_link(data: LinkCreate, session: Session = Depends(get_session)):
    if data.keyword:
        existing = session.exec(
            select(Link).where(Link.keyword == data.keyword)
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Keyword '{data.keyword}' já usada por: '{existing.title}'"
            )
    link = Link(**data.model_dump())
    link.url = str(data.url)
    session.add(link)
    session.commit()
    session.refresh(link)
    return link


@router.patch("/links/{link_id}", response_model=LinkRead, dependencies=[Depends(verify_admin)])
def update_link(link_id: int, data: LinkCreate, session: Session = Depends(get_session)):
    link = session.get(Link, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Link não encontrado")
    if data.keyword:
        existing = session.exec(
            select(Link)
            .where(Link.keyword == data.keyword)
            .where(Link.id != link_id)
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Keyword '{data.keyword}' já usada por: '{existing.title}'"
            )
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(link, field, str(value) if field == "url" else value)
    session.commit()
    session.refresh(link)
    return link


@router.delete("/links/{link_id}", dependencies=[Depends(verify_admin)])
def delete_link(link_id: int, session: Session = Depends(get_session)):
    link = session.get(Link, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Link não encontrado")
    session.delete(link)
    session.commit()
    return {"ok": True}


@router.post("/links/{link_id}/click")
def register_click(link_id: int, request: Request, session: Session = Depends(get_session)):
    link = session.get(Link, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Link não encontrado")
    if _rate_limit_click(request, link_id):
        session.exec(
            sa.update(Link)
            .where(Link.id == link_id)
            .values(clicks=Link.clicks + 1)
        )
        session.commit()
        session.refresh(link)
    return {"clicks": link.clicks}

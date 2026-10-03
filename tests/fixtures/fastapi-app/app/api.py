"""HTTP routes for the demo API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Item

router = APIRouter()


def get_session() -> Session:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@router.get("/items")
def list_items(session: Session = Depends(get_session)) -> list[dict[str, object]]:
    return [{"id": item.id, "name": item.name} for item in session.query(Item).all()]


@router.get("/items/{item_id}")
def get_item(item_id: int, session: Session = Depends(get_session)) -> dict[str, object]:
    item = session.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"id": item.id, "name": item.name}

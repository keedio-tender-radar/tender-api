"""Endpoints de acciones humanas (decisiones desde Telegram/dashboard)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from tender_contracts import ActionType

from tender_api.database import get_session
from tender_api.models import Tender, TenderAction
from tender_api.schemas import ActionCreate, ActionRead

router = APIRouter(prefix="/api/tenders", tags=["actions"])

# Acciones que cambian el estado de la licitación.
_STATUS_BY_ACTION = {
    ActionType.INTERESTED.value: "interested",
    ActionType.DISCARDED.value: "discarded",
    ActionType.PARTNER.value: "partner",
}
_VALID_ACTIONS = {a.value for a in ActionType}


@router.post("/{tender_id}/actions", response_model=ActionRead, status_code=201)
def create_action(tender_id: str, payload: ActionCreate, session: Session = Depends(get_session)):
    """Registra una acción humana; si procede, actualiza el estado de la licitación."""
    tender = session.get(Tender, tender_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    if payload.action not in _VALID_ACTIONS:
        raise HTTPException(422, f"Acción inválida: '{payload.action}'.")

    row = TenderAction(
        tender_id=tender_id, action=payload.action, actor=payload.actor, note=payload.note
    )
    session.add(row)
    new_status = _STATUS_BY_ACTION.get(payload.action)
    if new_status:
        tender.status = new_status
    session.commit()
    session.refresh(row)
    return ActionRead(
        id=row.id,
        tender_id=row.tender_id,
        action=row.action,
        actor=row.actor,
        note=row.note,
        created_at=row.created_at,
    )


@router.get("/{tender_id}/actions", response_model=list[ActionRead])
def list_actions(tender_id: str, session: Session = Depends(get_session)):
    if not session.get(Tender, tender_id):
        raise HTTPException(404, "Tender not found")
    rows = session.scalars(
        select(TenderAction)
        .where(TenderAction.tender_id == tender_id)
        .order_by(TenderAction.created_at.desc())
    ).all()
    return [
        ActionRead(
            id=r.id,
            tender_id=r.tender_id,
            action=r.action,
            actor=r.actor,
            note=r.note,
            created_at=r.created_at,
        )
        for r in rows
    ]

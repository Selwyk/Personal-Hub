from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import select, func
from sqlalchemy.orm import Session as DbSession

from app.database.models import Candidate, Batch, CollectorToken
from app.database.session import get_db
from app.modules.ingestion.schemas import BatchInput, ReviewInput, CandidateInput
from app.modules.ingestion.service import ingest, preview
from app.modules.journal.schemas import EntryInput, VersionInput, ReferenceInput
from app.modules.journal.service import create_entry
from app.security.service import signed_in, digest, now

router = APIRouter(prefix="/ingest", tags=["ingestion"])


def collector(request: Request, db: DbSession = Depends(get_db, scope="function")):
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(401, "Se requiere un token de recolector.")
    token = db.scalar(select(CollectorToken).where(
        CollectorToken.token_hash == digest(header[7:]), CollectorToken.revoked_at.is_(None))
        .with_for_update())
    if not token or token.scope != "journal:candidate:create" or (token.expires_at and token.expires_at <= now()):
        raise HTTPException(401, "Token no válido o caducado.")
    token.last_used_at = now()
    return token


@router.post("/batches")
def create_batch(body: BatchInput, token: CollectorToken = Depends(collector),
                 db: DbSession = Depends(get_db, scope="function")):
    return ingest(db, body, token.id)


@router.post("/preview", dependencies=[Depends(signed_in)])
def preview_batch(body: BatchInput):
    return preview(body)


@router.get("/batches", dependencies=[Depends(signed_in)])
def history(offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=200),
            db: DbSession = Depends(get_db, scope="function")):
    return {"items": [{"id": row.id, "import_key": row.import_key, "created_at": row.created_at,
                       "results": row.results} for row in db.scalars(select(Batch)
                       .order_by(Batch.created_at.desc(), Batch.id.desc()).offset(offset).limit(limit))],
            "total": db.scalar(select(func.count()).select_from(Batch))}


def candidate_dict(row):
    return {"id": row.id, "batch_id": row.batch_id, "status": row.status, "version": row.version,
            "content_hash": row.content_hash, "candidate": row.review_payload,
            "original_candidate": row.original_payload, "entry_id": row.entry_id,
            "created_at": row.created_at}


@router.get("/candidates", dependencies=[Depends(signed_in)])
def candidates(status: Literal["pending_review", "accepted", "discarded"] = "pending_review",
               offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=200),
               db: DbSession = Depends(get_db, scope="function")):
    query = select(Candidate).where(Candidate.status == status)
    return {"items": [candidate_dict(row) for row in db.scalars(query.order_by(
        Candidate.created_at.desc(), Candidate.id.desc()).offset(offset).limit(limit))],
        "total": db.scalar(select(func.count()).select_from(query.subquery()))}


def find_candidate(db, candidate_id):
    row = db.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update())
    if not row:
        raise HTTPException(404, "Candidato no encontrado.")
    return row


def reviewable(row, version):
    if row.status != "pending_review" or row.version != version:
        raise HTTPException(409, "El candidato ya se ha revisado o ha cambiado.")


@router.put("/candidates/{candidate_id}", dependencies=[Depends(signed_in)])
def edit(candidate_id: UUID, body: ReviewInput, db: DbSession = Depends(get_db, scope="function")):
    row = find_candidate(db, candidate_id)
    reviewable(row, body.version)
    # Source identity and evidence provenance remain immutable; review may edit content/classification.
    original = CandidateInput.model_validate(row.original_payload)
    for field in ("source_type", "source_reference", "external_id", "source_date", "collector_version", "raw_metadata"):
        if getattr(body.candidate, field) != getattr(original, field):
            raise HTTPException(422, "La procedencia original no se puede sustituir.")
    row.review_payload = body.candidate.model_dump(mode="json")
    row.version += 1
    return candidate_dict(row)


@router.post("/candidates/{candidate_id}/accept", dependencies=[Depends(signed_in)])
def accept(candidate_id: UUID, body: VersionInput, db: DbSession = Depends(get_db, scope="function")):
    row = find_candidate(db, candidate_id)
    reviewable(row, body.version)
    item = CandidateInput.model_validate(row.review_payload)
    provenance = item.raw_metadata.get("source_references")
    references = ([ReferenceInput.model_validate(value) for value in provenance]
                  if isinstance(provenance, list) and provenance else
                  [ReferenceInput(source_type=item.source_type,
                                  source_reference=item.source_reference,
                                  source_date=item.source_date, external_id=item.external_id,
                                  evidence_level=item.evidence_level)])
    entry = create_entry(db, EntryInput(occurred_on=item.occurred_on, title=item.title,
        summary=item.summary, content_markdown=item.detail_markdown, source=item.source_type,
        evidence_level=item.evidence_level, activity_type=item.activity_type,
        projects=item.projects, tags=item.tags, technologies=item.technologies,
        references=references))
    row.entry_id = entry.id
    row.status = "accepted"
    row.version += 1
    return {"entry_id": entry.id, "status": "draft"}


@router.post("/candidates/{candidate_id}/discard", dependencies=[Depends(signed_in)])
def discard(candidate_id: UUID, body: VersionInput, db: DbSession = Depends(get_db, scope="function")):
    row = find_candidate(db, candidate_id)
    reviewable(row, body.version)
    row.status = "discarded"
    row.version += 1
    return {"ok": True}

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, text, or_
from sqlalchemy.exc import IntegrityError

from app.database.models import Batch, Candidate
from app.modules.ingestion.schemas import CandidateInput, canonical_hash, content_hash


def validate_candidate(raw):
    candidate = CandidateInput.model_validate(raw)
    computed = content_hash(candidate)
    if candidate.content_hash and candidate.content_hash != computed:
        raise ValueError("content_hash does not match canonical content")
    return candidate, computed


def safe_error(exc):
    if isinstance(exc, ValidationError):
        return {"code": "invalid_candidate", "fields": [
            {"loc": list(e["loc"]), "type": e["type"]} for e in exc.errors()]}
    return {"code": "content_hash_mismatch"}


def preview(body):
    results = []
    for index, raw in enumerate(body.candidates):
        try:
            candidate, computed = validate_candidate(raw)
            results.append({"index": index, "status": "valid", "content_hash": computed,
                            "candidate": candidate.model_dump(mode="json")})
        except (ValidationError, ValueError) as exc:
            results.append({"index": index, "status": "invalid", "error": safe_error(exc)})
    return {"import_key": body.import_key, "results": results,
            "valid": sum(x["status"] == "valid" for x in results), "total": len(results)}


def existing_candidate(db, candidate, computed):
    clauses = [Candidate.content_hash == computed]
    if candidate.external_id:
        clauses.append(Candidate.external_id == candidate.external_id)
    matches = db.scalars(select(Candidate).where(Candidate.source_type == candidate.source_type,
        Candidate.source_reference == candidate.source_reference, or_(*clauses))).all()
    # An external identity conflict takes priority over unrelated matching content.
    for row in matches:
        if candidate.external_id and row.external_id == candidate.external_id and row.content_hash != computed:
            return "conflict", row
    return ("duplicate", matches[0]) if matches else (None, None)


def ingest(db, body, collector_id=None):
    request_hash = canonical_hash(body.model_dump(mode="json"))
    lock_key = int(canonical_hash({"key": body.import_key})[:16], 16) & ((1 << 63) - 1)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    batch = db.scalar(select(Batch).where(Batch.import_key == body.import_key))
    if batch:
        if batch.request_hash != request_hash or batch.collector_id != collector_id:
            raise HTTPException(409, "La clave del lote ya existe con otro contenido o propietario.")
        return {"id": str(batch.id), "replayed": True, "results": batch.results}
    batch = Batch(import_key=body.import_key, request_hash=request_hash, collector_id=collector_id)
    db.add(batch)
    db.flush()
    results = []
    for index, raw in enumerate(body.candidates):
        try:
            candidate, computed = validate_candidate(raw)
        except (ValidationError, ValueError) as exc:
            results.append({"index": index, "status": "invalid", "error": safe_error(exc)})
            continue
        status, existing = existing_candidate(db, candidate, computed)
        if existing:
            results.append({"index": index, "status": status,
                            **({"candidate_id": str(existing.id)} if status == "duplicate" else {})})
            continue
        try:
            with db.begin_nested():
                record = Candidate(batch_id=batch.id, source_type=candidate.source_type,
                    source_reference=candidate.source_reference, external_id=candidate.external_id,
                    content_hash=computed, original_payload=candidate.model_dump(mode="json"),
                    review_payload=candidate.model_dump(mode="json"), status="pending_review")
                db.add(record)
                db.flush()
            results.append({"index": index, "status": "created", "candidate_id": str(record.id)})
        except IntegrityError:
            status, existing = existing_candidate(db, candidate, computed)
            if not existing:
                raise
            results.append({"index": index, "status": status,
                            **({"candidate_id": str(existing.id)} if status == "duplicate" else {})})
    batch.results = results
    db.flush()
    return {"id": str(batch.id), "replayed": False, "results": results}

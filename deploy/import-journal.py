"""Transfer a Personal Hub JSON export without replacing target authentication.

Run inside the 1.1.0 API image with this script and the export mounted read-only.
Preview is the default. --apply explicitly preserves already published entries.
"""
import argparse
import json
import pathlib
import sys
from datetime import timezone
from typing import Literal

# A mounted script is outside /app, unlike the normal python -m app.cli entrypoint.
if pathlib.Path("/app/app").is_dir():
    sys.path.insert(0, "/app")

from fastapi import HTTPException  # noqa: E402
from pydantic import Field, ValidationError  # noqa: E402
from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from app.database.models import Entry, User  # noqa: E402
from app.database.session import session_factory  # noqa: E402
from app.modules.journal.schemas import EntryInput, EntryOut, normalize  # noqa: E402
from app.modules.journal.service import apply_input, entry_dict  # noqa: E402
from app.security.schemas import Input  # noqa: E402
from app.security.service import event  # noqa: E402

MAX_BYTES = 50 * 1024 * 1024
SCALARS = ("occurred_on", "title", "content_markdown", "summary", "source",
           "evidence_level", "activity_type", "technologies")


class TransferError(Exception):
    """Only public error codes and counts: never include input values."""


class Export(Input):
    schema_version: Literal[1]
    entries: list[EntryOut] = Field(max_length=10000)


def entry_input(row):
    return EntryInput.model_validate({
        **{key: getattr(row, key) for key in SCALARS},
        "projects": [p.name for p in row.projects],
        "tags": [t.name for t in row.tags],
        "references": row.references,
        "related_entry_ids": row.related_entry_ids,
    })


def validate_export(raw):
    if not isinstance(raw, dict) or type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        raise TransferError("unsupported_export_version")
    export = Export.model_validate(raw)
    seen = set()
    for row in export.entries:
        entry_input(row)  # Apply the same constraints as normal journal writes.
        if row.id.version != 7:
            raise TransferError("entry_id_requires_uuidv7")
        if row.id in seen:
            raise TransferError("duplicate_export_ids")
        seen.add(row.id)
        if row.id in row.related_entry_ids:
            raise TransferError("self_relation")
        if row.version < 1:
            raise TransferError("invalid_entry_version")
        if (row.status == "published") != (row.published_at is not None):
            raise TransferError("publication_inconsistent")
        for stamp in (row.created_at, row.updated_at, row.published_at):
            if stamp is not None and (stamp.tzinfo is None or stamp.utcoffset() is None):
                raise TransferError("timestamp_requires_timezone")
    return export.entries


def load_export(path):
    if path == "-":
        contents = sys.stdin.buffer.read(MAX_BYTES + 1)
    else:
        with pathlib.Path(path).open("rb") as stream:
            contents = stream.read(MAX_BYTES + 1)
    if len(contents) > MAX_BYTES:
        raise TransferError("export_exceeds_50_mib")
    return validate_export(json.loads(contents.decode("utf-8-sig")))


def signature(row):
    value = entry_input(row).model_dump(mode="json")
    # Project/tag IDs are local to each database; association order is not semantic.
    for key in ("projects", "tags"):
        value[key] = sorted(normalize(name) for name in value[key])
    value["related_entry_ids"] = sorted(set(value["related_entry_ids"]))
    value["references"] = sorted(value["references"], key=lambda r: json.dumps(r, sort_keys=True))
    value.update(status=row.status, version=row.version)
    for key in ("created_at", "updated_at", "published_at"):
        stamp = getattr(row, key)
        value[key] = stamp.astimezone(timezone.utc).isoformat() if stamp else None
    return value


def transfer(db, rows, apply=False):
    owner_query = select(User)
    if apply:
        owner_query = owner_query.with_for_update()
    owner = db.scalar(owner_query)
    if not owner:
        raise TransferError("target_owner_required")
    # All authenticated writes already take this same owner lock before entry locks.
    # Two concurrent imports therefore serialize without changing authentication.
    incoming = {row.id for row in rows}
    required = incoming | {related for row in rows for related in row.related_entry_ids}
    existing = {row.id: row for row in db.scalars(select(Entry).where(Entry.id.in_(required)))}
    if required - incoming - existing.keys():
        raise TransferError("related_entries_missing")
    new = []
    for row in rows:
        target = existing.get(row.id)
        if target:
            if signature(EntryOut.model_validate(entry_dict(db, target))) != signature(row):
                raise TransferError("existing_entry_conflict_no_overwrite")
        else:
            new.append(row)
    result = {"mode": "apply" if apply else "preview", "total": len(rows),
              "to_create": len(new), "already_present": len(rows) - len(new),
              "draft": sum(row.status == "draft" for row in rows),
              "published": sum(row.status == "published" for row in rows)}
    if not apply:
        return result
    # Insert all scalar rows first; forward links and cycles can then be restored.
    targets = {}
    for row in new:
        target = Entry(id=row.id, **{key: getattr(row, key) for key in SCALARS},
                       status=row.status, version=row.version, created_at=row.created_at,
                       updated_at=row.updated_at, published_at=row.published_at)
        db.add(target)
        targets[row.id] = target
    db.flush()
    for row in new:
        target = targets[row.id]
        apply_input(db, target, entry_input(row))
        # Normal editing sets updated_at to now; a transfer preserves the source stamp.
        target.updated_at = row.updated_at
    db.flush()
    if new:
        event(db, "journal_transferred", owner.id, created=len(new),
              already_present=len(rows) - len(new))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Private export file, or - to read from stdin")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        rows = load_export(args.input)
        with session_factory()() as db, db.begin():
            if not args.apply:
                db.execute(text("SET TRANSACTION READ ONLY"))
            result = transfer(db, rows, apply=args.apply)
        print(json.dumps(result))
    except TransferError as exc:
        print(json.dumps({"error": str(exc), "written": 0}), file=sys.stderr)
        return 2
    except (ValueError, ValidationError, OSError, HTTPException):
        print(json.dumps({"error": "invalid_export_or_file", "written": 0}), file=sys.stderr)
        return 2
    except SQLAlchemyError:
        print(json.dumps({"error": "database_error_transaction_rolled_back", "written": 0}),
              file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

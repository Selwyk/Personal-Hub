import argparse
import getpass
import json
import pathlib

from sqlalchemy import select

from app.database.models import User
from app.database.session import session_factory
from app.security.service import password_hasher, invalidate_after_restore, verify_after_restore, event
from app.core.config import settings
from app.modules.ingestion.schemas import BatchInput
from app.modules.ingestion.service import preview, ingest


def main():
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("create-user")
    importer = sub.add_parser("import-batch")
    importer.add_argument("--input", default=None)
    importer.add_argument("--apply", action="store_true", help="Explicitly insert pending candidates")
    importer.add_argument("--details", action="store_true", help="Print private preview to terminal")
    security = sub.add_parser("security")
    security.add_argument("action", choices=["invalidate-after-restore", "verify-after-restore"])
    args = parser.parse_args()
    if args.command == "import-batch":
        path = args.input or settings().import_input_path
        if not path:
            raise SystemExit("Provide --input or IMPORT_INPUT_PATH")
        source = pathlib.Path(path)
        if source.stat().st_size > 2 * 1024 * 1024:
            raise SystemExit("Import exceeds 2 MiB")
        batch = BatchInput.model_validate_json(source.read_text(encoding="utf-8"))
        result = preview(batch)
        if args.details:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"valid": result["valid"], "total": result["total"],
                              "mode": "preview", "errors": [r for r in result["results"]
                              if r["status"] == "invalid"]}))
        if result["valid"] != result["total"]:
            raise SystemExit(2)
        if not args.apply:
            return
        with session_factory()() as db:
            with db.begin():
                inserted = ingest(db, batch)
            print(json.dumps({"mode": "apply", "replayed": inserted["replayed"],
                              "results": inserted["results"]}))
        return
    with session_factory()() as db:
        with db.begin():
            if args.command == "create-user":
                if db.scalar(select(User.id)):
                    raise SystemExit("User already exists")
                username = input("Usuario: ").strip()
                password = getpass.getpass("Contraseña (mínimo 15 caracteres): ")
                confirm = getpass.getpass("Repite la contraseña: ")
                if not username or len(username) > 100 or len(password) < 15 or len(password) > 1024 or password != confirm:
                    raise SystemExit("Invalid user/password; no changes made")
                user = User(username=username, password_hash=password_hasher.hash(password))
                db.add(user)
                db.flush()
                event(db, "initial_user_created", user.id)
                print("Usuario creado. Completa el alta MFA en el navegador.")
            elif args.action == "invalidate-after-restore":
                result = invalidate_after_restore(db)
            else:
                result = verify_after_restore(db)
        if args.command == "security":
            print(json.dumps(result))
            if result.get("valid") is False:
                raise SystemExit(1)


if __name__ == "__main__":
    main()

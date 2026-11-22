"""Product boundaries verified without another checkout."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.instance import database_name, role_name
from app.core.product import DOMAIN, NAME
from app.main import app


def test_product_identity_and_professional_adapter_boundary():
    assert app.openapi()["info"]["title"] == NAME
    professional = importlib.util.find_spec("app.activity")
    assert (professional is not None) == (DOMAIN == "work")


def test_other_product_domain_rejected_before_database_access():
    identity = uuid4()
    other = "work" if DOMAIN == "personal" else "personal"
    with pytest.raises(ValidationError):
        Settings(isolation_required=True, instance_id=identity, instance_domain=other,
                 database_url=f"postgresql+psycopg://{role_name(identity, 'app')}:fixture@db/{database_name(identity, other)}")


def test_prepare_cli_does_not_create_another_product_profile(tmp_path):
    source = Path("/deploy/instances.py")
    if not source.exists():
        source = Path(__file__).resolve().parents[2] / "deploy/instances.py"
    other = "work" if DOMAIN == "personal" else "personal"
    destination = tmp_path / "wrong-product"
    result = subprocess.run([sys.executable, str(source), "prepare", "--root", str(destination),
        "--domain", other, "--origin", "http://localhost:15473", "--port", "15473", "--synthetic"],
        capture_output=True)
    assert result.returncode != 0
    assert not destination.exists()


def test_migration_history_is_repository_local():
    versions = Path("migrations/versions")
    assert len(list(versions.glob("*.py"))) == 4
    metadata = json.loads(Path("product.json").read_text())
    assert metadata["domain"] == DOMAIN

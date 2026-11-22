"""Repository-local product identity; no imports from another repository."""
import json
from pathlib import Path

PRODUCT = json.loads((Path(__file__).resolve().parents[2] / "product.json").read_text(encoding="utf-8"))
NAME = PRODUCT["name"]
DOMAIN = PRODUCT["domain"]
VERSION = PRODUCT["version"]
if DOMAIN not in ("personal", "work"):
    raise RuntimeError("Invalid product identity")

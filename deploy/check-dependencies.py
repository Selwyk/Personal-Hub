"""Check release metadata and OSV for every selected Python package; no private input."""
import json
import pathlib
import re
import urllib.request

root = pathlib.Path(__file__).resolve().parents[1]
packages = {}
for path in (root / "backend").glob("requirements*.lock"):
    for line in path.read_text().splitlines():
        match = re.match(r"^([\w.-]+)(?:\[[^]]+\])?==([^\s\\]+)", line)
        if match:
            packages[match[1]] = match[2]
issues = []
for name, version in sorted(packages.items()):
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json") as response:
        metadata = json.load(response)
    if not metadata["urls"] or any(f["yanked"] for f in metadata["urls"]):
        issues.append({"package": name, "version": version, "issue": "yanked/missing"})
    body = json.dumps({"package": {"name": name, "ecosystem": "PyPI"}, "version": version}).encode()
    request = urllib.request.Request("https://api.osv.dev/v1/query", body,
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        advisories = json.load(response).get("vulns", [])
    for advisory in advisories:
        issues.append({"package": name, "version": version, "issue": advisory["id"]})
print(json.dumps({"packages_checked": len(packages), "issues": issues}, indent=2))
raise SystemExit(bool(issues))

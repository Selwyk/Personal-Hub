"""Validate source boundaries without reading private runtime data."""
import ast
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {".git", ".runtime", "node_modules", "dist", "__pycache__", ".pytest_cache", ".ruff_cache", "test-results", "playwright-report"}
errors = []
public = [p for p in ROOT.rglob("*") if p.is_file() and not any(part in IGNORED for part in p.relative_to(ROOT).parts)]
for p in public:
    relative = p.relative_to(ROOT)
    if p.is_symlink():
        errors.append("Source symlink: " + str(relative))
    if p.name == ".env" or p.suffix in {".dump", ".enc", ".key", ".pem"}:
        errors.append("Private file: " + str(relative))
    if p.suffix not in {".py", ".ts", ".tsx", ".css", ".json", ".md", ".yaml", ".in", ".lock", ".conf", ".ps1", ".html"} and p.name != "Dockerfile":
        continue
    text = p.read_text(encoding="utf-8")
    if re.search(r"(?i)[A-Z]:[\\/](?:Users|Dev|OwnCloud)[\\/]", text):
        errors.append("Private absolute path: " + str(relative))
    if "backend/app" in relative.as_posix() and re.search(r"""(?i)\b(?:pat|password|secret|token)\s*=\s*["'][A-Za-z0-9_+/=-]{32,}["']""", text):
        errors.append("Potential literal credential: " + str(relative))

package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
for group in ("dependencies", "devDependencies"):
    for name, value in package.get(group, {}).items():
        if value.startswith(("file:", "link:", "workspace:", "../", "/")):
            errors.append("Local package dependency: " + name)

modules = {}
for p in (ROOT / "backend/app").rglob("*.py"):
    relative = p.relative_to(ROOT / "backend").with_suffix("")
    parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
    modules[".".join(parts)] = p
graph = {name: set() for name in modules}
for name, p in modules.items():
    tree = ast.parse(p.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Import):
            graph[name].update(alias.name for alias in node.names if alias.name in modules)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            base = node.module or ""
            if base in modules:
                graph[name].add(base)
            graph[name].update(base + "." + alias.name for alias in node.names if base + "." + alias.name in modules)
def cycles(graph, label):
    visited, active = set(), set()
    def visit(name):
        if name in active:
            errors.append(label + " import cycle at " + name)
            return
        if name in visited:
            return
        active.add(name)
        for other in graph[name]:
            visit(other)
        active.remove(name)
        visited.add(name)
    for name in graph:
        visit(name)
cycles(graph, "Python top-level")
typescript = {p.resolve(): p for p in (ROOT / "frontend/src").glob("*.ts*")}
front_graph = {str(p): set() for p in typescript}
for p in typescript:
    for match in re.finditer(r"""(?:from\s*|import\s*\()\s*["'](\.[^"']+)["']""", p.read_text(encoding="utf-8")):
        target = (p.parent / match.group(1)).resolve()
        if not target.is_relative_to(ROOT):
            errors.append("Frontend import leaves repository")
        candidates = [Path(str(target) + suffix) for suffix in (".ts", ".tsx", ".d.ts", "")]
        resolved = next((c for c in candidates if c in typescript), None)
        if resolved is not None:
            front_graph[str(p)].add(str(resolved))
cycles(front_graph, "TypeScript")

product = json.loads((ROOT / "backend/product.json").read_text(encoding="utf-8"))
if product["domain"] == "personal" and (ROOT / "backend/app/activity").exists():
    errors.append("Professional collector present in personal product")
if product["domain"] == "personal":
    for p in (ROOT / "backend").rglob("*.py"):
        if "__pycache__" not in p.parts and re.search(r"(?:from|import)\s+app\.activity", p.read_text(encoding="utf-8")):
            errors.append("Personal code imports professional collector")
if errors:
    print("\n".join(errors))
    raise SystemExit(1)
print(f'Boundary/privacy PASS: {len(public)} public files; {len(modules)} Python modules; no local package dependencies or top-level import cycles')

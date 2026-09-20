"""Skill manifest 生成与渲染。"""

from __future__ import annotations

from pathlib import Path


_SKIP_NAMES = {
    ".git", "__pycache__", "node_modules", ".pytest_cache",
    ".venv", "venv", "dist", "build", ".idea", ".vscode",
}
_SCRIPT_SUFFIXES = {".py", ".sh", ".js", ".ts", ".rb", ".pl"}
_DOCSTRING_HEAD_BYTES = 2048


def _extract_script_purpose(head: str, suffix: str) -> str:
    lines = head.splitlines()
    if suffix == ".py":
        in_docstring = False
        quote = ""
        doc_lines: list[str] = []
        for raw in lines[:30]:
            line = raw.strip()
            if not in_docstring:
                if line.startswith('"""') or line.startswith("'''"):
                    quote = line[:3]
                    rest = line[3:]
                    if rest.endswith(quote) and len(rest) >= 3:
                        return rest[:-3].strip()[:200]
                    if quote in rest:
                        return rest.split(quote, 1)[0].strip()[:200]
                    in_docstring = True
                    if rest:
                        doc_lines.append(rest)
                    continue
                if line.startswith("#") and not line.startswith("#!"):
                    return line.lstrip("# ").strip()[:200]
            else:
                if line.endswith(quote):
                    doc_lines.append(line[:-3].strip())
                    return " ".join(s for s in doc_lines if s).strip()[:200]
                doc_lines.append(line)
        return " ".join(s for s in doc_lines[:3] if s).strip()[:200]

    if suffix in (".sh", ".js", ".ts", ".rb", ".pl"):
        for raw in lines[:20]:
            line = raw.strip()
            if not line or line.startswith("#!"):
                continue
            if line.startswith("#") or line.startswith("//"):
                return line.lstrip("# /").strip()[:200]
    return ""


def _read_head(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:_DOCSTRING_HEAD_BYTES]
    except OSError:
        return ""


def build_skill_manifest_from_dir(skill_dir: Path, *, skill_id: str | None = None) -> dict:
    skill_dir = Path(skill_dir).resolve()
    files: list[Path] = []

    if skill_dir.exists() and skill_dir.is_dir():
        for item in skill_dir.rglob("*"):
            if not item.is_file():
                continue
            try:
                rel_parts = item.relative_to(skill_dir).parts
            except ValueError:
                continue
            if any(part in _SKIP_NAMES or part.startswith(".") for part in rel_parts):
                continue
            files.append(item)

    rel_paths = sorted(str(f.relative_to(skill_dir)).replace("\\", "/") for f in files)
    scripts = []
    references = []
    data_files = []
    other_files = []
    for path in files:
        rel = str(path.relative_to(skill_dir)).replace("\\", "/")
        if rel.startswith("scripts/") and path.suffix in _SCRIPT_SUFFIXES:
            scripts.append({
                "path": rel,
                "purpose": _extract_script_purpose(_read_head(path), path.suffix) or "",
                "bytes": path.stat().st_size,
            })
        elif rel.startswith("references/"):
            references.append(rel)
        elif rel.startswith("data/"):
            data_files.append(rel)
        elif rel not in {
            "SKILL.md",
            "policy_pack.yaml",
            "intent.md",
            "policy.yaml",
            "task-contract.json",
            "task-review-state.json",
        }:
            other_files.append(rel)

    key_files = {
        "skill_md": "SKILL.md" in rel_paths,
        "policy_pack": "policy_pack.yaml" in rel_paths,
        "intent_md": "intent.md" in rel_paths,
        "policy_yaml": "policy.yaml" in rel_paths,
        "task_contract": "task-contract.json" in rel_paths,
        "task_review_state": "task-review-state.json" in rel_paths,
        "main_script": "scripts/main.py" in rel_paths,
        "main_test": "tests/test_main.py" in rel_paths,
    }

    return {
        "skill_id": skill_id or skill_dir.name,
        "path": str(skill_dir),
        "total_files": len(rel_paths),
        "key_files": key_files,
        "script_count": len(scripts),
        "reference_count": len(references),
        "data_file_count": len(data_files),
        "scripts": scripts,
        "references": references[:20],
        "data_files": data_files[:20],
        "other_files": other_files[:20],
        "all_files": rel_paths,
    }


def render_manifest_summary(manifest: dict) -> str:
    lines = ["# Skill Manifest"]
    lines.append(f"- skill_id: {manifest.get('skill_id', '')}")
    lines.append(f"- total_files: {manifest.get('total_files', 0)}")
    key_files = manifest.get("key_files") or {}
    lines.append(
        "- key_files: "
        + ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in key_files.items())
    )
    if manifest.get("scripts"):
        lines.append("- scripts:")
        for item in (manifest.get("scripts") or [])[:10]:
            lines.append(f"  - {item['path']}: {item.get('purpose') or '(无说明)'}")
    if manifest.get("references"):
        lines.append("- references: " + ", ".join((manifest.get("references") or [])[:8]))
    if manifest.get("other_files"):
        lines.append("- other_files: " + ", ".join((manifest.get("other_files") or [])[:8]))
    return "\n".join(lines)

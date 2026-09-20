import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_frontend_design_consistency.py"


def load_audit_module():
    spec = importlib.util.spec_from_file_location("design_consistency_audit", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_design_consistency_audit_covers_pages_components_and_gpt_imagegen_remediation():
    module = load_audit_module()
    audit = module.build_audit(include_components=True)

    assert audit["summary"]["page_count"] >= 100
    assert audit["summary"]["component_count"] >= 100
    assert audit["summary"]["file_count"] >= 200
    assert audit["top_offenders"]

    imagegen_rows = {Path(row["file"]).name: row for row in audit["hall_gpt_imagegen"]}
    assert "HallGptImageGenForm.vue" in imagegen_rows
    assert imagegen_rows["HallGptImageGenForm.vue"]["shell_adopted"] is True
    assert imagegen_rows["HallGptImageGenForm.vue"]["ai_token_refs"] > 0
    assert imagegen_rows["HallGptImageGenForm.vue"]["score"] >= 80

    for name in ("HallGptImageGenChat.vue", "HallGptImageGenWorkspace.vue", "HallGptImageGenLegacy.vue"):
        row = imagegen_rows[name]
        assert row["severity"] == "pass"
        assert row["hard_hex"] == 0
        assert row["rgba_refs"] == 0
        assert row["hard_gradients"] == 0
        assert row["arco_empty"] == 0
        assert row["ai_token_refs"] > 0

    # The remaining drift is structural size/px density, not off-system colors or raw empty states.
    assert any("px 尺寸密度过高" in "；".join(row["reasons"]) for row in audit["hall_gpt_imagegen"])

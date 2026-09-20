import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "register_project_enterprise_scale_inventory.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("project_enterprise_scale_inventory", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_scale_inventory_project_payload_spreads_departments_and_capabilities():
    module = _load_module()
    rows = [module.project_payload(index, 10) for index in range(1000)]

    assert len({row["id"] for row in rows}) == 1000
    assert len({row["department_id"] for row in rows}) == 10
    assert len({row["type"] for row in rows}) == 4
    assert all(row["visibility"] == "department" for row in rows)
    assert all(row["status"] == "published" for row in rows)
    assert all("ai.cheap.generate" in row["metadata_json"]["capabilities"] for row in rows)


def test_scale_inventory_ids_are_project_schema_safe():
    module = _load_module()
    assert module.project_id(999) == "scale_inv_0999"
    assert len(module.project_id(999)) <= 42
    assert module.department_id(9) == "scale-dept-09"

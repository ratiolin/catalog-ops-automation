import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "shadowbot" / "catalog_odoo_rpa.py"
SPEC = importlib.util.spec_from_file_location("catalog_odoo_rpa", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_operation_key_is_stable_and_separates_recovery_from_failure() -> None:
    written = MODULE._operation_key("run-1", "item-1", "written")
    failed = MODULE._operation_key("run-1", "item-1", "failed", "timeout")

    assert written == MODULE._operation_key("run-1", "item-1", "written")
    assert failed == MODULE._operation_key("run-1", "item-1", "failed", "timeout")
    assert written != failed
    assert len(written) <= 128


def test_required_rejects_blank_secret() -> None:
    try:
        MODULE._required({"rpa_token": "  "}, "rpa_token")
    except ValueError as exc:
        assert str(exc) == "missing argument: rpa_token"
    else:
        raise AssertionError("blank secret should be rejected")

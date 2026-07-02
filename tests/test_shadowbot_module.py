import importlib.util
import inspect
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


def test_shadowbot_entrypoint_has_no_python_defaults() -> None:
    """ShadowBot 6.0.30 otherwise emits an empty keyword argument."""
    parameters = inspect.signature(MODULE.run).parameters.values()
    assert all(parameter.default is inspect.Parameter.empty for parameter in parameters)


def test_parse_odoo_login_url_extracts_origin_and_database() -> None:
    base_url, database = MODULE._parse_odoo_login_url(
        "http://127.0.0.1:18069/web/login?db=catalog_erp"
    )

    assert base_url == "http://127.0.0.1:18069"
    assert database == "catalog_erp"


def test_parse_odoo_login_url_requires_database() -> None:
    try:
        MODULE._parse_odoo_login_url("http://127.0.0.1:18069/web/login")
    except ValueError as exc:
        assert "missing db" in str(exc)
    else:
        raise AssertionError("database-less Odoo URL should be rejected")


def test_parse_price_accepts_currency_text_and_blank_values() -> None:
    assert MODULE._parse_price("¥ 1,299.50") == 1299.50
    assert MODULE._parse_price("") == 0.0


def test_product_type_selection_uses_supported_odoo_value() -> None:
    fields = {
        "detailed_type": {
            "readonly": False,
            "selection": [["service", "Service"], ["consu", "Goods"]],
        }
    }

    assert MODULE._pick_product_type_value(fields) == ("detailed_type", "consu")


def test_build_product_values_uses_discovered_writable_fields(monkeypatch) -> None:
    fields = {
        "name": {"readonly": False},
        "default_code": {"readonly": False},
        "list_price": {"readonly": False},
        "description_sale": {"readonly": False},
        "sale_ok": {"readonly": False},
        "purchase_ok": {"readonly": True},
        "detailed_type": {
            "readonly": False,
            "selection": [["consu", "Goods"]],
        },
    }
    monkeypatch.setattr(MODULE, "_odoo_fields", lambda _odoo, _model: fields)

    values = MODULE._build_product_values(
        {},
        {
            "item_id": "item-1",
            "sku": "TEST-001",
            "listing_title": "Test product",
            "price": "¥99.50",
            "category": "test",
            "stock": "10",
            "selling_points": "stable",
            "keywords": "test",
        },
        "full",
    )

    assert values["default_code"] == "TEST-001"
    assert values["list_price"] == 99.50
    assert values["detailed_type"] == "consu"
    assert values["sale_ok"] is True
    assert "purchase_ok" not in values

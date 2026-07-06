import importlib.util
import inspect
from pathlib import Path

# ---------------------------------------------------------------------------
# Load modules (shadowbot is now a real package)
# ---------------------------------------------------------------------------

def _load_module(name, filename):
    path = Path(__file__).parents[1] / 'shadowbot' / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RPA = _load_module('catalog_odoo_rpa', 'catalog_odoo_rpa.py')
ODOO = _load_module('odoo_adapter', 'odoo_adapter.py')
PB = _load_module('product_builder', 'product_builder.py')

# ---------------------------------------------------------------------------
# catalog_odoo_rpa tests
# ---------------------------------------------------------------------------

def test_operation_key_is_stable_and_separates_recovery_from_failure():
    written = RPA._operation_key('run-1', 'item-1', 'written')
    failed = RPA._operation_key('run-1', 'item-1', 'failed', 'timeout')
    assert written == RPA._operation_key('run-1', 'item-1', 'written')
    assert failed == RPA._operation_key('run-1', 'item-1', 'failed', 'timeout')
    assert written != failed
    assert len(written) <= 128


def test_required_rejects_blank_secret():
    try:
        RPA._required({'rpa_token': '  '}, 'rpa_token')
    except ValueError as exc:
        assert str(exc) == 'missing argument: rpa_token'
    else:
        raise AssertionError('blank secret should be rejected')


def test_shadowbot_entrypoint_has_no_python_defaults():
    parameters = inspect.signature(RPA.run).parameters.values()
    assert all(
        parameter.default is inspect.Parameter.empty
        for parameter in parameters
    )

# ---------------------------------------------------------------------------
# odoo_adapter tests
# ---------------------------------------------------------------------------

def test_parse_odoo_login_url_extracts_origin_and_database():
    base_url, database = ODOO._parse_odoo_login_url(
        'http://127.0.0.1:18069/web/login?db=catalog_erp'
    )
    assert base_url == 'http://127.0.0.1:18069'
    assert database == 'catalog_erp'


def test_parse_odoo_login_url_requires_database():
    try:
        ODOO._parse_odoo_login_url('http://127.0.0.1:18069/web/login')
    except ValueError as exc:
        assert 'missing db' in str(exc)
    else:
        raise AssertionError('database-less Odoo URL should be rejected')

# ---------------------------------------------------------------------------
# product_builder tests
# ---------------------------------------------------------------------------

def test_parse_price_accepts_currency_text_and_blank_values():
    assert PB.parse_price("¥ 1,299.50") == 1299.50
    assert PB.parse_price('') == 0.0


def test_product_type_selection_uses_supported_odoo_value():
    from shadowbot.odoo_adapter import OdooClient

    class FakeOdoo(OdooClient):
        def __init__(self):
            pass
        def fields_get(self, model):
            return {
                'detailed_type': {
                    'readonly': False,
                    'selection': [['service', 'Service'], ['consu', 'Goods']],
                }
            }
        def field_writable(self, model, name):
            return True
        def selection_keys(self, field_def):
            return OdooClient.selection_keys(field_def)

    odoo = FakeOdoo()
    assert PB.pick_product_type_value(odoo) == ('detailed_type', 'consu')


def test_build_product_values_uses_discovered_writable_fields():
    from shadowbot.odoo_adapter import OdooClient

    class FakeOdoo(OdooClient):
        def __init__(self):
            pass
        def fields_get(self, model):
            return {
                'name': {'readonly': False},
                'default_code': {'readonly': False},
                'list_price': {'readonly': False},
                'description_sale': {'readonly': False},
                'sale_ok': {'readonly': False},
                'purchase_ok': {'readonly': True},
                'detailed_type': {
                    'readonly': False,
                    'selection': [['consu', 'Goods']],
                },
            }
        def field_writable(self, model, name):
            fields = self.fields_get(model)
            if name not in fields:
                return False
            return not bool(fields[name].get('readonly'))

    odoo = FakeOdoo()
    values = PB.build_product_values(
        odoo,
        {
            'item_id': 'item-1',
            'sku': 'TEST-001',
            'listing_title': 'Test product',
            'price': '¥99.50',
            'category': 'test',
            'stock': '10',
            'selling_points': 'stable',
            'keywords': 'test',
        },
        'full',
    )
    assert values['default_code'] == 'TEST-001'
    assert values['list_price'] == 99.50
    assert values['detailed_type'] == 'consu'
    assert values['sale_ok'] is True
    assert 'purchase_ok' not in values

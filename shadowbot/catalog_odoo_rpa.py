# -*- coding: utf-8 -*-

import builtins
import csv
import hashlib
import io
import json
import re
import xmlrpc.client
from urllib import error, request
from urllib.parse import parse_qs, urlsplit

try:
    import xbot
    from xbot import sleep
    from xbot import print as xbot_print
    print = xbot_print
except ImportError:
    xbot = None
    print = builtins.print

    def sleep(_seconds):
        return None


def _required(args, name):
    value = str(args.get(name, "")).strip()
    if not value:
        raise ValueError("missing argument: {}".format(name))
    return value


def _item_value(item, name, default=""):
    return str(item.get(name, default) or "").strip()


def _request_bytes(url, rpa_token, timeout=30):
    req = request.Request(url, headers={"X-RPA-Token": rpa_token})
    with request.urlopen(req, timeout=timeout) as response:
        return response.read()


def _request_json(url, rpa_token, payload, timeout=30):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error = None

    for attempt in range(1, 4):
        req = request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "X-RPA-Token": rpa_token,
            },
        )

        try:
            with request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))

        except error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                detail = ""
            last_error = "HTTPError {} {}".format(exc.code, detail[:500])
            if attempt < 3:
                sleep(attempt)

        except error.URLError as exc:
            last_error = exc
            if attempt < 3:
                sleep(attempt)

    raise RuntimeError("callback failed after retries: {}".format(last_error))


def _approved_items(api_base_url, run_id, rpa_token):
    url = "{}/v1/catalog/runs/{}/approved.csv".format(
        api_base_url.rstrip("/"),
        run_id,
    )
    content = _request_bytes(url, rpa_token).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(content)))


def _operation_key(run_id, item_id, result, detail=""):
    if result == "written":
        suffix = "written-v1"
    else:
        digest = hashlib.sha1(detail.encode("utf-8")).hexdigest()[:12]
        suffix = "failed-{}".format(digest)

    return "catalog-rpa:{}:{}:{}".format(run_id, item_id, suffix)


def _callback(api_base_url, run_id, rpa_token, item, result, record_id=None, message=None):
    item_id = _item_value(item, "item_id")
    detail = (message or "")[:500]

    payload = {
        "results": [
            {
                "item_id": item_id,
                "operation_key": _operation_key(run_id, item_id, result, detail),
                "result": result,
                "erp_record_id": record_id,
                "error": detail or None,
            }
        ]
    }

    url = "{}/v1/catalog/runs/{}/erp-results".format(
        api_base_url.rstrip("/"),
        run_id,
    )
    return _request_json(url, rpa_token, payload)


def _parse_odoo_login_url(odoo_login_url):
    parsed = urlsplit(odoo_login_url)

    if not parsed.scheme or not parsed.netloc:
        raise ValueError("invalid odoo_login_url: {}".format(odoo_login_url))

    base_url = "{}://{}".format(parsed.scheme, parsed.netloc)

    query = parse_qs(parsed.query or "")
    db = ""

    if "db" in query and query["db"]:
        db = query["db"][0]

    if not db:
        raise ValueError("missing db in odoo_login_url: {}".format(odoo_login_url))

    return base_url, db


def _parse_price(value):
    text = str(value or "").strip()
    if not text:
        return 0.0

    cleaned = re.sub(r"[^0-9.\-]", "", text)
    if not cleaned or cleaned in ("-", ".", "-."):
        return 0.0

    return float(cleaned)


def _compact_fault(exc):
    text = str(getattr(exc, "faultString", "") or exc)
    text = text.replace("\\n", "\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    important = []

    for line in lines:
        if (
            line.startswith("ValueError:")
            or line.startswith("KeyError:")
            or line.startswith("TypeError:")
            or line.startswith("UserError:")
            or line.startswith("ValidationError:")
            or line.startswith("AccessError:")
            or "odoo.exceptions" in line
            or "Wrong value" in line
            or "Invalid field" in line
            or "required field" in line
            or "Missing required" in line
        ):
            important.append(line)

    if important:
        return " | ".join(important[-4:])[:900]

    if lines:
        return " | ".join(lines[-6:])[:900]

    return str(exc)[:900]


def _odoo_connect(odoo_login_url, username, password):
    base_url, db = _parse_odoo_login_url(odoo_login_url)

    common = xmlrpc.client.ServerProxy(
        "{}/xmlrpc/2/common".format(base_url),
        allow_none=True,
    )

    uid = common.authenticate(db, username, password, {})

    if not uid:
        raise RuntimeError("odoo authentication failed for user {}".format(username))

    models = xmlrpc.client.ServerProxy(
        "{}/xmlrpc/2/object".format(base_url),
        allow_none=True,
    )

    return {
        "base_url": base_url,
        "db": db,
        "uid": uid,
        "password": password,
        "models": models,
        "fields_cache": {},
    }


def _odoo_execute(odoo, model, method, args=None, kwargs=None):
    if args is None:
        args = []
    if kwargs is None:
        kwargs = {}

    try:
        return odoo["models"].execute_kw(
            odoo["db"],
            odoo["uid"],
            odoo["password"],
            model,
            method,
            args,
            kwargs,
        )
    except xmlrpc.client.Fault as exc:
        raise RuntimeError(
            "Odoo RPC failed: {}.{}: {}".format(
                model,
                method,
                _compact_fault(exc),
            )
        )


def _odoo_fields(odoo, model):
    cache = odoo.get("fields_cache", {})
    if model in cache:
        return cache[model]

    fields = _odoo_execute(
        odoo,
        model,
        "fields_get",
        [],
        {
            "attributes": [
                "type",
                "required",
                "readonly",
                "selection",
                "string",
            ]
        },
    )

    cache[model] = fields
    odoo["fields_cache"] = cache
    return fields


def _field_exists(fields, name):
    return name in fields


def _field_writable(fields, name):
    if name not in fields:
        return False
    return not bool(fields[name].get("readonly"))


def _selection_keys(field_def):
    raw = field_def.get("selection") or []
    keys = []

    for item in raw:
        if isinstance(item, (list, tuple)) and item:
            keys.append(str(item[0]))
        else:
            keys.append(str(item))

    return keys


def _pick_product_type_value(fields):
    if "detailed_type" in fields and _field_writable(fields, "detailed_type"):
        keys = _selection_keys(fields["detailed_type"])

        if "consu" in keys:
            return "detailed_type", "consu"

        if "product" in keys:
            return "detailed_type", "product"

        if keys:
            print("warning: detailed_type selections: {}".format(keys))

    if "type" in fields and _field_writable(fields, "type"):
        keys = _selection_keys(fields["type"])

        if "consu" in keys:
            return "type", "consu"

        if "product" in keys:
            return "type", "product"

        if keys:
            print("warning: type selections: {}".format(keys))

    return None, None


def _search_template_by_sku(odoo, sku):
    tmpl_fields = _odoo_fields(odoo, "product.template")

    if _field_exists(tmpl_fields, "default_code"):
        records = _odoo_execute(
            odoo,
            "product.template",
            "search_read",
            [[["default_code", "=", sku]]],
            {
                "fields": ["id", "name", "default_code"],
                "limit": 1,
            },
        )

        if records:
            return records[0]["id"]

    return None


def _search_variant_by_sku(odoo, sku):
    try:
        variant_fields = _odoo_fields(odoo, "product.product")
    except Exception:
        return None

    if not _field_exists(variant_fields, "default_code"):
        return None

    records = _odoo_execute(
        odoo,
        "product.product",
        "search_read",
        [[["default_code", "=", sku]]],
        {
            "fields": ["id", "product_tmpl_id", "default_code"],
            "limit": 1,
        },
    )

    if not records:
        return None

    product_tmpl_id = records[0].get("product_tmpl_id")

    if isinstance(product_tmpl_id, (list, tuple)) and product_tmpl_id:
        return product_tmpl_id[0]

    return None


def _product_exists(odoo, sku):
    product_id = _search_template_by_sku(odoo, sku)
    if product_id:
        return product_id

    product_id = _search_variant_by_sku(odoo, sku)
    if product_id:
        return product_id

    return None


def _build_note(item):
    return "\n".join(
        [
            "来源：客户反馈作品集模拟商品上架流程",
            "SKU：{}".format(_item_value(item, "sku")),
            "源品类：{}".format(_item_value(item, "category")),
            "源库存：{}".format(_item_value(item, "stock")),
            "卖点：{}".format(_item_value(item, "selling_points")),
            "关键词：{}".format(_item_value(item, "keywords")),
            "追踪 item_id：{}".format(_item_value(item, "item_id")),
        ]
    )


def _build_product_values(odoo, item, mode):
    fields = _odoo_fields(odoo, "product.template")

    sku = _item_value(item, "sku")
    title = _item_value(item, "listing_title")
    price = _parse_price(_item_value(item, "price"))
    note = _build_note(item)

    if not sku:
        raise ValueError("missing sku for item_id={}".format(_item_value(item, "item_id")))

    if not title:
        raise ValueError("missing listing_title for sku={}".format(sku))

    values = {
        "name": title,
    }

    if mode in ("full", "no_type") and _field_writable(fields, "default_code"):
        values["default_code"] = sku

    if mode in ("full", "no_type") and _field_writable(fields, "list_price"):
        values["list_price"] = price

    if mode == "full":
        field_name, field_value = _pick_product_type_value(fields)
        if field_name and field_value:
            values[field_name] = field_value

        if _field_writable(fields, "description_sale"):
            values["description_sale"] = note

        if _field_writable(fields, "description"):
            values["description"] = note

        if _field_writable(fields, "sale_ok"):
            values["sale_ok"] = True

        if _field_writable(fields, "purchase_ok"):
            values["purchase_ok"] = True

    return values


def _write_template_values(odoo, product_id, values):
    if not values:
        return False

    _odoo_execute(
        odoo,
        "product.template",
        "write",
        [[product_id], values],
        {},
    )
    return True


def _write_variant_sku(odoo, product_id, sku):
    try:
        variant_fields = _odoo_fields(odoo, "product.product")
    except Exception as exc:
        print("warning: cannot read product.product fields: {}".format(str(exc)[:300]))
        return False

    if not _field_writable(variant_fields, "default_code"):
        return False

    variants = _odoo_execute(
        odoo,
        "product.product",
        "search_read",
        [[["product_tmpl_id", "=", product_id]]],
        {
            "fields": ["id", "default_code"],
            "limit": 1,
        },
    )

    if not variants:
        return False

    variant_id = variants[0]["id"]

    _odoo_execute(
        odoo,
        "product.product",
        "write",
        [[variant_id], {"default_code": sku}],
        {},
    )

    return True


def _after_create_update_optional_fields(odoo, product_id, item):
    fields = _odoo_fields(odoo, "product.template")

    sku = _item_value(item, "sku")
    price = _parse_price(_item_value(item, "price"))
    note = _build_note(item)

    values = {}

    if _field_writable(fields, "default_code"):
        values["default_code"] = sku

    if _field_writable(fields, "list_price"):
        values["list_price"] = price

    if _field_writable(fields, "description_sale"):
        values["description_sale"] = note

    if _field_writable(fields, "description"):
        values["description"] = note

    if _field_writable(fields, "sale_ok"):
        values["sale_ok"] = True

    if _field_writable(fields, "purchase_ok"):
        values["purchase_ok"] = True

    field_name, field_value = _pick_product_type_value(fields)
    if field_name and field_value and _field_writable(fields, field_name):
        values[field_name] = field_value

    try:
        _write_template_values(odoo, product_id, values)
    except Exception as exc:
        print("warning: optional template write failed: {}".format(str(exc)[:400]))

    try:
        _write_variant_sku(odoo, product_id, sku)
    except Exception as exc:
        print("warning: variant sku write failed: {}".format(str(exc)[:400]))


def _create_template_with_fallback(odoo, item):
    attempts = [
        ("full", _build_product_values(odoo, item, "full")),
        ("no_type", _build_product_values(odoo, item, "no_type")),
        ("minimal", _build_product_values(odoo, item, "minimal")),
    ]

    last_error = None

    for name, values in attempts:
        try:
            print("create attempt {} values keys: {}".format(name, sorted(values.keys())))

            product_id = _odoo_execute(
                odoo,
                "product.template",
                "create",
                [values],
                {},
            )

            if name != "full":
                _after_create_update_optional_fields(odoo, product_id, item)

            return product_id

        except Exception as exc:
            last_error = exc
            print("warning: create attempt {} failed: {}".format(name, str(exc)[:600]))

    raise RuntimeError("all product.template create attempts failed: {}".format(last_error))


def _create_product(odoo, item):
    sku = _item_value(item, "sku")

    existing_id = _product_exists(odoo, sku)
    if existing_id:
        return "odoo-existing-{}".format(existing_id)

    product_id = _create_template_with_fallback(odoo, item)

    return "odoo-product-{}".format(product_id)


def main(args):
    api_base_url = _required(args, "api_base_url")
    run_id = _required(args, "run_id")
    rpa_token = _required(args, "rpa_token")
    odoo_login_url = _required(args, "odoo_login_url")
    _required(args, "odoo_product_list_url")
    odoo_username = _required(args, "odoo_username")
    odoo_password = _required(args, "odoo_password")

    max_items = int(args.get("max_items", 100))
    dry_run = str(args.get("dry_run", "false")).strip().lower() in (
        "1",
        "true",
        "yes",
        "y",
    )

    items = _approved_items(api_base_url, run_id, rpa_token)[:max_items]

    print("approved items: {}".format(len(items)))

    if dry_run:
        return {
            "approved": len(items),
            "written": 0,
            "failed": 0,
            "dry_run": True,
        }

    odoo = _odoo_connect(
        odoo_login_url,
        odoo_username,
        odoo_password,
    )

    print("odoo connected: db={}, uid={}".format(odoo["db"], odoo["uid"]))

    written = 0
    failed = 0

    for item in items:
        sku = _item_value(item, "sku")

        try:
            if not sku:
                raise ValueError("missing sku for item_id={}".format(_item_value(item, "item_id")))

            print("processing: {}".format(sku))

            record_id = _create_product(odoo, item)

            _callback(
                api_base_url,
                run_id,
                rpa_token,
                item,
                "written",
                record_id=record_id,
            )

            written += 1
            print("written: {} {}".format(sku, record_id))

        except Exception as exc:
            message = "{}: {}".format(type(exc).__name__, exc)[:500]

            _callback(
                api_base_url,
                run_id,
                rpa_token,
                item,
                "failed",
                message=message,
            )

            failed += 1
            print("failed: {} {}".format(sku, message))

    return {
        "approved": len(items),
        "written": written,
        "failed": failed,
        "dry_run": False,
    }


def run(
    api_base_url,
    run_id,
    rpa_token,
    odoo_login_url,
    odoo_product_list_url,
    odoo_username,
    odoo_password,
    max_items,
    dry_run,
):
    return main({
        "api_base_url": api_base_url,
        "run_id": run_id,
        "rpa_token": rpa_token,
        "odoo_login_url": odoo_login_url,
        "odoo_product_list_url": odoo_product_list_url,
        "odoo_username": odoo_username,
        "odoo_password": odoo_password,
        "max_items": max_items,
        "dry_run": dry_run,
    })

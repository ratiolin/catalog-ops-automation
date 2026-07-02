"""影刀 Python 模块：把已通过门禁的候选商品写入本地 Odoo 沙箱。

边界：模块只读取 ``approved.csv``；不会接触校验失败的商品。每条 Odoo
写入都会用稳定 operation_key 回调 API，服务端再以条件状态转换落库。
"""

import csv
import hashlib
import io
import json
import re
from urllib import error, request

try:
    import xbot
    from xbot import print, sleep
except ImportError:  # 允许仓库测试纯函数；实际运行必须在影刀中。
    xbot = None
    print = print

    def sleep(_seconds):
        return None


def _required(args, name):
    value = str(args.get(name, "")).strip()
    if not value:
        raise ValueError("missing argument: {}".format(name))
    return value


def _request_bytes(url, rpa_token, timeout=30):
    req = request.Request(url, headers={"X-RPA-Token": rpa_token})
    with request.urlopen(req, timeout=timeout) as response:
        return response.read()


def _request_json(url, rpa_token, payload, timeout=30):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-RPA-Token": rpa_token,
        },
    )
    last_error = None
    for attempt in range(1, 4):
        try:
            with request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (error.URLError, error.HTTPError) as exc:
            last_error = exc
            if attempt < 3:
                sleep(attempt)
    raise RuntimeError("callback failed after retries: {}".format(last_error))


def _approved_items(api_base_url, run_id, rpa_token):
    url = "{}/v1/catalog/runs/{}/approved.csv".format(
        api_base_url.rstrip("/"), run_id
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
    detail = (message or "")[:500]
    payload = {
        "results": [
            {
                "item_id": item["item_id"],
                "operation_key": _operation_key(
                    run_id, item["item_id"], result, detail
                ),
                "result": result,
                "erp_record_id": record_id,
                "error": detail or None,
            }
        ]
    }
    url = "{}/v1/catalog/runs/{}/erp-results".format(
        api_base_url.rstrip("/"), run_id
    )
    return _request_json(url, rpa_token, payload)


def _login(browser, login_url, username, password):
    browser.navigate(login_url, load_timeout=30)
    login_fields = browser.find_all_by_css("input[name='login']", timeout=3)
    if not login_fields:
        return
    login_fields[0].input(username, simulative=False, delay_after=0.2)
    browser.find_by_css("input[name='password']", timeout=5).input(
        password, simulative=False, delay_after=0.2
    )
    browser.find_by_css("button[type='submit']", timeout=5).click(delay_after=1)
    browser.wait_load_completed(timeout=30)


def _search_product(browser, product_list_url, sku):
    browser.navigate(product_list_url, load_timeout=30)
    search = browser.find_by_css("input.o_searchview_input", timeout=15)
    search.input(sku, simulative=False, delay_after=0.2)
    browser.execute_javascript(
        """
        var e = document.querySelector('input.o_searchview_input');
        if (e) {
          e.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', code:'Enter', bubbles:true}));
          e.dispatchEvent(new KeyboardEvent('keyup', {key:'Enter', code:'Enter', bubbles:true}));
        }
        """
    )
    sleep(1)
    for card in browser.find_all_by_css(".o_kanban_record", timeout=2):
        if sku in card.get_text():
            return True
    return False


def _create_product(browser, item):
    browser.find_by_css("button.o-kanban-button-new", timeout=10).click(delay_after=1)
    browser.find_by_css("textarea[id^='name_']", timeout=10).input(
        item["listing_title"], simulative=False, delay_after=0.1
    )
    browser.find_by_css("input[id^='list_price_']", timeout=10).input(
        item["price"], simulative=False, delay_after=0.1
    )
    browser.find_by_css("input[id^='default_code_']", timeout=10).input(
        item["sku"], simulative=False, delay_after=0.1
    )
    note = "\n".join(
        [
            "来源：客户反馈作品集模拟商品上架流程",
            "SKU：{}".format(item["sku"]),
            "源品类：{}".format(item["category"]),
            "源库存：{}".format(item["stock"]),
            "卖点：{}".format(item["selling_points"]),
            "关键词：{}".format(item["keywords"]),
            "追踪 item_id：{}".format(item["item_id"]),
        ]
    )
    browser.execute_javascript(
        """
        var e = document.querySelector('.note-editable');
        if (e) {
          e.innerText = %s;
          e.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText'}));
        }
        """ % json.dumps(note, ensure_ascii=False)
    )
    browser.find_by_css("button.o_form_button_save", timeout=10).click(delay_after=1)
    sleep(1)
    url = browser.get_url()
    match = re.search(r"/(\d+)(?:\?.*)?$", url or "")
    return "odoo-product-{}".format(match.group(1) if match else item["sku"])


def main(args):
    if xbot is None:
        raise RuntimeError("This module must run inside ShadowBot")

    api_base_url = _required(args, "api_base_url")
    run_id = _required(args, "run_id")
    rpa_token = _required(args, "rpa_token")
    login_url = _required(args, "odoo_login_url")
    product_list_url = _required(args, "odoo_product_list_url")
    odoo_username = _required(args, "odoo_username")
    odoo_password = _required(args, "odoo_password")
    max_items = int(args.get("max_items", 100))
    dry_run = str(args.get("dry_run", "false")).lower() in ("1", "true", "yes")

    items = _approved_items(api_base_url, run_id, rpa_token)[:max_items]
    print("approved items: {}".format(len(items)))
    if dry_run:
        return {"approved": len(items), "written": 0, "failed": 0, "dry_run": True}

    browser = xbot.web.create(login_url, mode="cef", load_timeout=30)
    _login(browser, login_url, odoo_username, odoo_password)
    written = 0
    failed = 0
    for item in items:
        try:
            if _search_product(browser, product_list_url, item["sku"]):
                record_id = "odoo-existing-{}".format(item["sku"])
            else:
                record_id = _create_product(browser, item)
            _callback(
                api_base_url,
                run_id,
                rpa_token,
                item,
                "written",
                record_id=record_id,
            )
            written += 1
            print("written: {}".format(item["sku"]))
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
            print("failed: {} {}".format(item["sku"], message))

    return {"approved": len(items), "written": written, "failed": failed, "dry_run": False}


def run(
    api_base_url,
    run_id,
    rpa_token,
    odoo_login_url,
    odoo_product_list_url,
    odoo_username,
    odoo_password,
    max_items=100,
    dry_run="false",
):
    """供影刀“调用模块 → 指定函数”界面直接映射的显式入口。"""
    return main(
        {
            "api_base_url": api_base_url,
            "run_id": run_id,
            "rpa_token": rpa_token,
            "odoo_login_url": odoo_login_url,
            "odoo_product_list_url": odoo_product_list_url,
            "odoo_username": odoo_username,
            "odoo_password": odoo_password,
            "max_items": max_items,
            "dry_run": dry_run,
        }
    )

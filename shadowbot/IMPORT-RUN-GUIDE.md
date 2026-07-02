# 影刀导入与运行

本流程只处理 API 返回的 `approved.csv`，不会接触 `validation_failed` 商品。实际写入目标是仅监听本机的 Odoo Community 沙箱。

## 一次性创建应用

1. 影刀首页新建可视化应用，名称设为「商品上架写入 Odoo（沙箱）」。
2. 在流程面板新建 Python 模块，模块名设为 `catalog_odoo_rpa`。
3. 将 `catalog_odoo_rpa.py` 全文粘贴到模块并保存。
4. 主流程添加「调用模块」，选择该模块的 `run` 函数。
5. 将下面参数逐项映射；`rpa_token` 和 `odoo_password` 使用密码输入参数或私有全局变量，不写进源码。

| 参数 | 值 |
|---|---|
| `api_base_url` | `http://127.0.0.1:18200` |
| `run_id` | 使用 API 新一轮回放返回的 UUID |
| `rpa_token` | `/srv/stack/catalog-ops-automation/.env` 中的 `RPA_TOKEN` |
| `odoo_login_url` | `http://127.0.0.1:18069/web/login?db=catalog_erp` |
| `odoo_product_list_url` | `http://127.0.0.1:18069/odoo/action-282` |
| `odoo_username` | `admin` |
| `odoo_password` | 当前本地 Odoo 管理员密码，初始化默认值为 `admin` |
| `max_items` | 首次验证填 `1`，全量填 `20` |
| `dry_run` | 首次填 `true`，确认读到 20 条后改为 `false` |

## 验证顺序

1. `dry_run=true`、`max_items=20`：运行日志应返回 `approved=20, written=0, failed=0`，此步不写 Odoo、不回调状态。
2. `dry_run=false`、`max_items=1`：确认 Odoo 新增 1 个商品，API 中对应记录变为 `erp_written`。
3. `dry_run=false`、`max_items=20`：完成其余商品；已写入 SKU 会先查询并复用，不重复创建。
4. 再运行一次：API operation key 被复用，Odoo 仍不增加重复 SKU。
5. 打开 Metabase「商品上架自动化运营看板」，确认 ERP 回写结果出现 `written`。

## 失败与恢复

- Odoo 写入失败：模块以错误摘要回调 `erp_failed`，不伪装成功；修复页面定位后可再次运行。
- Odoo 已写入但回调暂时失败：再次运行会先按 SKU 找到已有商品，然后用稳定的 `written-v1` operation key 补回调。
- API 回调重试三次仍失败：流程停止并保留 Odoo 数据，避免在未知状态下继续批量写入。
- `validation_failed` 商品不会出现在下载清单，因此影刀无法绕过确定性门禁。

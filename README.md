# 商品上架运营自动化

一个面向数字化运营/实施岗位的合成沙箱项目：商品 CSV 经 Dify 生成候选文案，FastAPI 执行确定性异常校验，影刀 RPA 将通过项写入 Odoo Community，Metabase 展示流程结果。

```text
商品 CSV → Dify 候选文案 → 确定性校验 → ready_for_rpa
→ 影刀操作 Odoo 商品模块 → 幂等结果回传 → Metabase 看板
```

## 边界

- Dify 只生成标题、三个卖点和关键词，不决定是否写入 ERP。
- 只有确定性校验通过的记录才能进入影刀下载清单。
- 影刀写入前按 SKU 查询 Odoo；重复执行不得创建重复商品。
- Odoo、数据和账号均为本地合成沙箱，不代表生产 ERP 实施。
- Metabase 是只读分析界面；不通过 BI 看板修改业务状态。

## 本地端口

- API/OpenAPI：<http://127.0.0.1:18200/docs>
- Odoo：<http://127.0.0.1:18069>
- Metabase：<http://127.0.0.1:18300>

## 启动

```bash
cp .env.example .env
# 设置 CATALOG_DB_PASSWORD 与 RPA_TOKEN
docker compose up -d catalog-postgres
docker compose --profile init run --rm catalog-odoo-init
docker compose up -d --build catalog-api catalog-worker catalog-odoo catalog-metabase
```

未配置 `DIFY_CATALOG_WORKFLOW_API_KEY` 时，只有在 `ALLOW_DEMO_COPYWRITER=true` 的明确演示模式下才使用 `demo_rules`，来源会写入数据库。真实平台证据必须配置 Dify Key 并确认 `draft_source=dify`。

## 初始化只读 BI 账号

Metabase 自身数据库和业务数据源分离。业务数据源使用 `metabase_reader`，只有 `CONNECT/USAGE/SELECT` 权限：

```bash
set -a && source .env && set +a
docker compose exec -T catalog-postgres \
  psql -v ON_ERROR_STOP=1 -v reader_password="$METABASE_READER_PASSWORD" \
  -U catalog -d catalog_ops -f /dev/stdin < tools/configure_metabase_reader.sql
```

当前沙箱已经创建「商品上架自动化运营看板」，包含最新批次状态、异常原因、品类状态和 ERP 回写结果四张卡片。Metabase 管理员账号保存在本地 `.env`，不得提交。

## 运行合成回放

```bash
uv run python tools/generate_sample_catalog.py
uv run python tools/run_catalog_demo.py
```

固定样本为 30 条：20 条源数据有效、10 条确定性异常（含重复 SKU）。预期结果是 `ready_for_rpa=20`、`validation_failed=10`。演示规则与 Dify 结果通过 `draft_source` 明确区分。

## 影刀写入 Odoo

影刀 6.0.30 Python 模块位于 `shadowbot/catalog_odoo_rpa.py`，界面创建与回放步骤见 `shadowbot/IMPORT-RUN-GUIDE.md`。模块具备：

- 写入前按 SKU 查询，防止 Odoo 重复商品；
- 每条写入后立即回调，不把整批未知状态一次性提交；
- 成功与失败使用不同的稳定 operation key，失败恢复后仍可转为成功；
- API 通过条件状态转换和唯一键再次兜底幂等。

## 验证

```bash
uv run pytest -q
uv run ruff check .
docker compose exec -T catalog-api alembic check
curl -fsS http://127.0.0.1:18200/health
curl -fsS http://127.0.0.1:18300/api/health
```

这是合成沙箱和机制证据，不声称真实 ERP 生产上线、真实业务提效或真实销售收益。

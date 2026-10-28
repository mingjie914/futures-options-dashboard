# API 合同

规范基础路径为 `/api/seats/v1`，与 futures/options 平级；`/api/v1` 暂保留只读兼容别名，不再用于前端默认请求。所有业务接口只读当前已发布快照，并返回统一信封：

```json
{
  "meta": {
    "trade_date": "2026-07-28",
    "batch_id": 9,
    "data_backend": "local",
    "complete": true,
    "warnings": []
  },
  "data": {}
}
```

## 页面接口

| 路径 | 主要返回内容 |
|---|---|
| `/meta/status` | 批次、交易日、更新时间、来源、完整性和告警 |
| `/meta/options` | 可用日期、中文品种、组合和标签定义 |
| `/views/combination` | 核心指标、三类席位榜、组合定义与信号、四类背离卡、研究摘要 |
| `/views/market-signals` | 多/空单腿异动、反转、严重度计数、全市场品种矩形树图 |
| `/views/term-structure` | 合约结构、跨期反向席位完整腿、净持仓变化 Top10 |
| `/views/validation` | F2/净 CR5 的 5/10 日规则行、分位、截点和版本 |
| `/seats/{seat_id}/history` | 席位历史序列 |
| `/products/{product_code}/history` | 品种历史序列 |

`product_code=all`、`ALL`、`*` 与省略品种等价。全市场席位榜先按席位聚合；特定品种保持交易日 × 品种 × 席位粒度。

## 组合页关键字段

- `combination_signals`：`direction`、`net_position`、`net_change`、`action_code`、成员数和已观察成员数。
- `combination_positions`：所选日全市场品种 × 活跃分组，返回 `net_position`、`net_change`、`long_count`、`short_count`、`flat_count`、`missing_count`。前两项为已披露成员合计，全缺为 null；多空/平按单席净持仓正负/零计数。矩阵展示所有组，只有数据完整且全员同向时突出；背离仍取严格 `combination_signals`，不使用不完整合计。
- `divergence_cards`：四类组合背离的计数及品种列表。
- `research_summary`：F2 分位、净 CR5 分位、规律库截点、异动数量和跨期反向数量。
- `net_position_top`：包含 `previous_net_position`、`net_position_change` 和 `entry_state`。

## 状态与缺失

- 比例分母为零时返回 `null`，不以 0 代替。
- F2 指标只在分母有效时为 `ready`；F2 历史规律方向仍保持 `unavailable`。
- 历史规则使用结构化 `ok`、`sample_insufficient`、`unavailable` 状态。
- 席位行的 `tags` 带来源、置信度、有效期、证据和备注。
- 跨期行的 `legs_json` 返回该席位当日完整非零合约链。
- 跨期 `contracts` 增加 `close_price`、`price_date`，按交易日、品种、合约符号从同一快照行情表精确匹配；缺失、非有限数或非正价格返回 `null`，不使用主力价或其他日期替代。
- 市场 `treemap` 增加 `open_interest`、`previous_open_interest`、`open_interest_change_rate`、`previous_interest_date`、两日 `interest_contract_count` / `previous_interest_contract_count` 及 `interest_contracts_changed`。变化率为本日真实合约持仓合计 / 上一可用行情交易日合计 − 1，剔除本行情源 8888/9998/9999 合成序列；缺前值、前值为零、存在空或负持仓时不发布变化率。合约集合变化保留结果并明确提示，不能推断已确认全量覆盖或资金流。

前端按页签读取：联合席位读取 options、combination、validation；市场信号读取 options、market-signals；跨期结构读取 options、term-structure。历史仅在打开席位时读取。所有参与同屏的响应须具有相同 data_version；切换中自动重试一次，持续不一致则明确报错。

## 后续云端协商所需的最小约定

以下是当前页面需求，不是已上线广播合同：

- 席位属于日频数据：启动、筛选变化和用户刷新时请求；历史在点击席位后请求，当前上限 500 条。暂不要求秒级广播或 WebSocket。
- 品种使用 `RB.SHFE` 等交易所限定标识，合约使用 `RB2610.SHFE`，席位使用既有稳定 ID；持仓与变化单位为手，比例在 API 保留小数值，在适配层转换为百分数。
- 必需事实：交易日、品种/合约/席位标识、多空持仓及变化、行情收盘与合约期限元数据。组合、标签及 F2/CR5/历史验证复用既有规则与版本，不能由前端猜测。
- 历史计算保留既定 504 日分位、1260 日规律库和 25 交易日隔离；展示历史上限不能当成离线计算的历史下限。
- `trade_date` 表示数据交易日，`updated_at` 是快照发布时间，`batch_id` 是数据批次；复制或启动 API 不刷新这些值。HTTP 可达、数据完整、数据新鲜分别判断，不能以连接成功替代行情更新时间。
- 正式域名、挂载路径、云端来源、鉴权、广播协议仍未定。保留 `NEXT_PUBLIC_API_BASE_URL`；默认使用同源 `/api/seats/v1`；本地 Vite 代理已映射，线上反向代理仍需配置验收。
- 公开 JSON 或快照加增量仅作后续候选，不引入未使用的协议层。当前沿用 HTTP API 保留完整筛选与历史查询，待真实数据合同后做最小接入和端到端验证。

## HTTP 压缩与版本校验

- JSON 满 1 KiB 才启用 gzip level 5；识别 `Accept-Encoding` 的 q 值（含 gzip;q=0），输出 `Vary: Accept-Encoding`。一个响应只有一层压缩；反代应尊重已有 Content-Encoding，不再次压缩。错误响应不进入结果缓存。
- 弱 ETag 覆盖 API 表示版本、数据版本、路径/全部查询参数及 JSON 内容；gzip 与 identity 共享语义验证器。If-None-Match 支持列表、弱比较及 *；304 无正文、无 Content-Encoding。
- `Cache-Control: private, no-cache`；跨域暴露 ETag、X-Data-Version，允许 If-None-Match。前端有 8 MiB 的文本缓存预算并显式条件请求；缺基线的意外 304 会重取完整响应。
- `meta.data_version` / `X-Data-Version` 是查询实现版本 QUERY_VERSION、清单内容（含批次、发布时刻、来源指纹、metric_version/rule_version）与快照文件大小、mtime/ctime 的摘要，不泄露目录。单次请求绑定同一清单，发布期间 SQL 和 meta 不会读到两个批次。生产必须以新不可变快照和原子清单发布同日修订，不能原地修改在线库。
- 服务端每进程 LRU 默认预算 128 MiB，计入 JSON、gzip、键及条目预算开销；超大响应不缓存。相同键的并发未命中合并一次查询；所有未命中重查询共用 1 个槽，可配 2 个。同步路由在线程池等待，不阻塞 ASGI event loop。此预算不等于整个进程 RSS 上限。
- DuckDB 在线只读连接默认 1 线程、256 MB，可通过配置改为 512 MB；内存上限属于 DuckDB 引擎而非整个进程。多 API 进程会成倍增加缓存/并发预算，初始部署使用 1 个 worker。

## 增量边界与待办

当前实现的是页面分块按需读取、缓存复用和条件重验证。304 表示该响应未变，**不是行级增量**；版本变化时重新取得该块完整 JSON，包含修订和删除后的最终结果。

跨批次行级增量尚未实现。上线前需要源端稳定主键、修订/删除日志及覆盖/缺失语义，再冻结 baseline_version、target_version、upsert/delete、错基线全量回退、同日修订和断线续传合同；不能仅按日期追加，也不能把缺数当删除。保留 FastAPI/ASGI 和未来广播接入边界，本轮不引入常驻 WS/CTP 或新数据源。HTTP gzip 不代表 WS 帧压缩已验证。

## 2026-10-28 本地验收与服务器接入

- `python -m pytest backend/tests -q`：23 项通过，含新旧路径、压缩解压一致、q=0、304/CORS、筛选隔离、批次/同日发布/公式/查询版本失效、发布时单请求快照固定、1/2 查询槽、同键合并、LRU 字节预算、清理边界，以及无源数据分组重算、原快照不变、重叠成员、零信号、失败不切换、混合/净零/缺席分组。
- TypeScript 检查、Vite 构建与 `node --test tests/rendered-html.test.mjs` 9 项通过；含按页请求、304 基线、版本混用重试/失败/恢复、历史截止日期/版本、跨期反转标签保留及分组缺席/净零/同向映射。
- 浏览器实际请求 `/api/seats/v1`：联合页 3 块、市场/跨期各 2 块；200 带 gzip，重复刷新 304，已检查页签切换。没有把静态产物检查冒充浏览器验收。
- b14 本地 TCP 测试（CF.CZCE，2026-10-26）：combination JSON 74,303 B → gzip 8,416 B；market 58,880 B → 10,875 B；term（含反转标签所需信号）30,227 B → 4,373 B。304 本地请求约 1.8–2.3 ms。不是 SG/冷盘/公网并发基准；详细运行证据在本地 `var/transport-local-check.json`，不入库。
- 后端代码可独立复制 `backend/src/seat_dashboard` 和 `backend/pyproject.toml`，使用已有经验证快照，无须复制研究输入；运行 `python -m uvicorn seat_dashboard.main:app --app-dir backend/src --host 127.0.0.1 --port <私有端口> --workers 1`。设置 DASHBOARD_RUNTIME_ROOT、DASHBOARD_DUCKDB_THREADS=1、DASHBOARD_DUCKDB_MEMORY_MB=256、DASHBOARD_QUERY_CONCURRENCY=1、DASHBOARD_CACHE_BYTES=134217728；前端默认同源新路径，反代路径需原样转发。
- 云端会话提供并已读取的 `E:/云服务器/甲骨文/核验-20261027/sg-api-optimized-20261028.json`：b14 私有 SG 回环测试 72 次全部 200，10 并发热缓存 p95 18.39 ms、RSS 峰值 124.1 MiB；临时 Caddy 下 gzip/identity 一致、304 空正文、ETag 透传、404 非 HTML、同日清单修订失效通过。该报告不含新五组 b15，也不是公网容量验收。
- 仍待验收：正式路由/公网 HTTPS/跨域、持续更新、离线任务错峰/磁盘预算/回滚。HTTP 结果不外推 WS 帧压缩和实时行情能力。

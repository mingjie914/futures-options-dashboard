# 期货席位行为监控架构

## 架构决策

当前采用“显式配置的只读源 + 离线快照 + 页面级 API”。Parquet 是当前构建输入，DuckDB 是可迁移的服务快照，FastAPI 提供 REST 风格 HTTP 查询，静态 React 页面只理解 JSON 合同。生产输入来自权威 RDS 的不可变导出批次，本地研究目录仅用于开发测试，不是云端在线依赖。

```text
derive_product_daily ─┐
derive_product_seat ──┤
derive_main_contract ─┼→ ParquetSource → SnapshotBuilder → SnapshotValidator
raw_seat_position ────┤                                  │
market 1d ────────────┘                                  ▼
                                      DuckDBPublisher → batch DuckDB
                                                               │
                                                     current.json 原子切换
                                                               │
                                                               ▼
                                                     DuckDBReader → FastAPI
                                                                       │
                                                                       ▼
                                                            adapter.ts → UI
```

## 模块边界

- `ports`：定义来源、发布和页面查询合同。
- `adapters/source`：只读 Parquet 数据源。
- `jobs/SnapshotBuilder`：唯一的大数据计算入口，生成指标、组合、信号和验证表。
- `adapters/store/duckdb`：不可变快照发布与只读页面查询。
- `FastAPI`：统一信封、参数校验、历史查询。
- `frontend/src/seats/adapter.ts`：后端字段到前端领域模型的唯一映射层。
- `Dashboard.tsx`：只消费前端领域模型，不包含大数据公式。

## 快照发布

每次构建创建独立临时 DuckDB，完成必需表、公式、配置成员、行数和完整性检查后，才移动为 `seat_dashboard_bNNNNNNNN.duckdb` 并原子替换 `var/manifests/current.json`。构建失败不会影响上一成功批次；API 根据指针自动重连新快照。

新清单的 `database_path` 相对运行目录；构建源目录只在离线构建时读取，API 初始化不探测源文件。`verify_snapshot --export-root` 复制一份已校验数据库及精简清单，复制前后校验 SHA-256；迁移后在无原始源目录环境复验 API。此产物是私有后端数据，不是可直接公开的静态数据包。

当前完整构建同时保留历史品种与席位事实，用于相邻交易日净持仓变化、反转识别、504 日分位和 1260 日规律库。API 请求期间不扫描原始 Parquet。

当 `raw_seat_position` 的最大交易日晚于 `derive_product_daily` / `derive_product_seat` 时，构建器仅对派生表最大日期之后的 raw 合约级记录做增量聚合，并与既有派生历史合并。该回退路径只写新 DuckDB 快照，不回写源 Parquet；重叠日回归测试要求 raw 聚合口径与派生表一致。

## 页面数据流

前端读取 `/meta/options` 和当前页的 `/views/combination`、`/views/market-signals` 或 `/views/term-structure`；合约结构页另读 `/views/validation`。适配层校验批次一致性，统一中文名称、百分比、标签、空值和红多绿空语义。席位历史按需加载，避免首屏携带大序列。

## 性能策略

- 原始席位事实只在离线构建读取，API 不扫描原始 Parquet。
- 页面查询命中已聚合的 `seat_dash_*` 表。
- 每个页面接口按业务用途限制 TopN。
- 组合一致性、异常倍数、跨期链和统计验证在快照阶段计算。
- 版本化快照允许无停机切换和快速回滚。

## RDS 消费边界

RDS 保持权威上游，消费任务提供不可变 Parquet 批次，`SnapshotBuilder → DuckDBPublisher → DuckDBReader` 完成离线派生和在线查询；API 不直连 RDS，不写上游原始表。P2 输入与修订处理见[输入契约](rds-migration.md)，生产发布与部署见[统一维护入口](../../docs/部署与统一维护入口.md)。

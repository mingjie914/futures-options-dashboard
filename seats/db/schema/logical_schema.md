# 看板逻辑 Schema

本地 DuckDB 和未来 RDS MySQL 使用同一组逻辑表名和字段语义。DuckDB 的物理 DDL 位于 `backend/src/seat_dashboard/adapters/store/duckdb/schema.sql`；MySQL 的可执行迁移位于 `db/migrations/mysql/001_create_seat_dashboard.sql`。

## 数据分层

```text
维度/治理：product_dim, contract_dim, seat_dim, seat_alias, tag_definition,
           seat_tag_assignment, combination, combination_member
事实集市：market_daily, product_daily, product_seat_daily,
           main_contract_seat_daily, contract_daily
结果集市：seat_signal_daily, term_signal_daily, rule_result_daily
审计发布：etl_run, publish_state
```

## 兼容约束

- 所有事实表以 `trade_date` 作为日期字段。
- 页面接口及逻辑字段保持存储无关；当前构建与查询 SQL 使用 DuckDB 方言，MySQL 迁移需另行实现和一致性验证。
- JSON 证据在 MySQL 迁移中保存为 `LONGTEXT`，读取时按 JSON 文本解释。
- 持仓、变动、样本数使用 `BIGINT`；比例、收益、置信度使用 `DOUBLE`。
- 大表主键包含 `trade_date`，满足 MySQL 日期分区的唯一键约束。
- DuckDB 快照不可原地更新；RDS 迁移使用 staging + 批次发布。

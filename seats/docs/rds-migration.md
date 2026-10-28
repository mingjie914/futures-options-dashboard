# RDS 迁移手册

当前 V1 只允许 `DASHBOARD_READ_STORE=duckdb` 与 `DASHBOARD_PUBLISH_STORES=duckdb`。MySQL 迁移、连接工厂、读写端口和同构表已预留；发布器及页面查询故意保持关闭，若提前配置 `mysql` 会明确失败，不能把“接口已预留”误认为“生产切换已完成”。

迁移实施顺序：

1. 在测试库运行仅创建 `seat_dash_*` 的迁移，并实现 `MySQLMartPublisher.publish()` 的幂等批次事务。
2. 补齐 `MySQLDashboardReader` 四个页面查询与历史，并执行 DuckDB/MySQL 合同测试。
3. 确认固定出口 IP、TLS CA、只读账号和独立发布账号权限。
4. 解除发布门禁，启用 `DASHBOARD_PUBLISH_STORES=duckdb,mysql`；API 仍读取 DuckDB。
5. 连续至少五个成功批次比较表行数、主键、持仓公式、聚合指标及 API 标准化结果。
6. 解除读取门禁，将 `DASHBOARD_READ_STORE` 切换为 `mysql` 并重启 API。
7. 继续保留本地快照；异常时改回 `duckdb`。

迁移不得修改 `futuresseatraw` 或现有上游上传程序。

## 当前 RDS 数据是否足够

RDS 只有 `raw_seat_position` 时，足以重建部分席位榜和跨期结构，但不足以直接、低延迟地支撑全部页面：统计验证还需要日行情，组合榜、CR5、历史与信号也不应在每个网页请求中扫描原始大表。

推荐不再照搬上传全部本地派生目录，而是继续在离线任务中读取：

- `raw_seat_position`：合约级席位事实与跨期结构；
- `derive_product_seat`、`derive_product_daily`、`derive_main_contract_seat`：本地构建提速；
- `futureshistoryprices1d`：历史收益验证。

随后仅向 RDS 发布版本化的 `seat_dash_*` 看板表。这样既保留现有 `futuresseatraw`，又避免 RDS 端重复存放所有中间文件。若未来要求完全在 RDS 内重算，则至少还需补充日行情和等价的品种/席位派生表。

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


## P2-only 离线构建

`python -m seat_dashboard.jobs.build_snapshot --p2-only --full --seat-root <P2根> --market-root <日行情根> --runtime-root <独立运行根>`。
输入仅需 `raw_seat_position/**/*.parquet` 和日行情 Parquet；必须是同一已校验、不可变的 RDS 导出批次。P3 在构建连接中临时计算，不写回源、不落第二套派生 Parquet，旧 P3 即使存在也不使用。全量重建覆盖同日修订/删除；输入内容 SHA-256 纳入发布身份。

品种/交易所统一大写；仅非汇总合约席位参与统计。相同键冲突拒发，完全重复行去重。郑商所三位年份按观测日最近唯一十年展开，距离五年存在歧义时拒发。主力由同品种同日日行情真实合约持仓最大值决定（排除连续/指数伪合约），行情修订按最大 sequence 取，最大 sequence 冲突拒发。主力并列/无法确定时价格和席位层标记 `main_unresolved`，无价格为 `missing_close`，唯一主力缺席位为 `missing_seat`；其他层正常发布，不改选次主力。原始全零席位保留，增减直接求和，不重复名称/D+1处理。数据结构冲突仍拒发并保留上一版本。

独立 `seat_dash_main_contract_coverage` 保留完整品种交易日轴，唯一主力真实收盘不因缺席位而丢失；同品种行情日与P2日期取并集（限P2历史范围），收益5/10交易日及25日隔离期不因空缺缩窗。派生计算版本为 `metrics-v2-reference-p2-calendar-v1`；缺失指标为null，覆盖状态随视图API返回。未取得上游溯源sidecar时 `backfill_status=unknown`，不宣称certified。当前命令离线重建已提供的输入历史，确保同日修订与删除生效；生产由既定消费作业同步变更分区（含最近5交易日/provisional及上游允许的历史修订窗口），不得把“最近5日”当唯一修订范围。

2026-10-29 RDS六日有界样本：443品种日/35,465品种席位，原始数值聚合0差异；RR 10/23与BC 10/26主力缺席位两处仅该层pending，其他层及4类API正常。证据保存在仓库外`audit-20261029/p2-all/result.json`。这不是全历史或生产上线验收。

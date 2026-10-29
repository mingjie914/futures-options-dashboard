# RDS 输入与离线快照

RDS 是权威上游，由消费任务导出不可变 Parquet 输入，离线构建并发布 DuckDB 服务快照。在线 API 只读取快照；本项目不向 RDS 发布看板表，也不直查上游。`DASHBOARD_READ_STORE` 与 `DASHBOARD_PUBLISH_STORES` 只支持 `duckdb`，其他值明确报错。

生产链路以[统一维护入口](../../docs/部署与统一维护入口.md)为准；本文件保留原路径供已有引用使用。

## P2-only 离线构建

`python -m seat_dashboard.jobs.build_snapshot --p2-only --full --seat-root <P2根> --market-root <日行情根> --runtime-root <独立运行根>`。
输入仅需 `raw_seat_position/**/*.parquet` 和日行情 Parquet；必须是同一已校验、不可变的 RDS 导出批次。P3 在构建连接中临时计算，不写回源、不落第二套派生 Parquet，旧 P3 即使存在也不使用。全量重建覆盖同日修订/删除；输入内容 SHA-256 纳入发布身份。

品种/交易所统一大写；仅非汇总合约席位参与统计。相同键冲突拒发，完全重复行去重。郑商所三位年份按观测日最近唯一十年展开，距离五年存在歧义时拒发。主力由同品种同日日行情真实合约持仓最大值决定（排除连续/指数伪合约），行情修订按最大 sequence 取，最大 sequence 冲突拒发。主力并列/无法确定时价格和席位层标记 `main_unresolved`，无价格为 `missing_close`，唯一主力缺席位为 `missing_seat`；其他层正常发布，不改选次主力。原始全零席位保留，增减直接求和，不重复名称/D+1处理。数据结构冲突仍拒发并保留上一版本。

独立 `seat_dash_main_contract_coverage` 保留完整品种交易日轴，唯一主力真实收盘不因缺席位而丢失；同品种行情日与P2日期取并集（限P2历史范围），收益5/10交易日及25日隔离期不因空缺缩窗。派生计算版本为 `metrics-v2-reference-p2-calendar-v2`；缺失指标为null，覆盖状态随视图API返回。未取得上游溯源sidecar时 `backfill_status=unknown`，不宣称certified。当前命令离线重建已提供的输入历史，确保同日修订与删除生效；生产由既定消费作业同步变更分区（含最近5交易日/provisional及上游允许的历史修订窗口），不得把“最近5日”当唯一修订范围。

2026-10-29 RDS六日有界样本：443品种日/35,465品种席位，原始数值聚合0差异；RR 10/23与BC 10/26主力缺席位两处仅该层pending，其他层及4类API正常。证据保存在仓库外`audit-20261029/p2-all/result.json`。这不是全历史或生产上线验收。

离线构建与重分类共用独立预算：`SEATS_BUILD_THREADS=1`、`SEATS_BUILD_MEMORY_MB=512`、`SEATS_BUILD_TEMP_MB=512`；不复用 API 缓存限制。临时溢写位于本批 staging 子目录，完成/异常关闭后清理空目录；TEMP_MB=0 可禁用溢写。DuckDB内存限制不是进程RSS硬上限，云端仍由任务cgroup约束，并保留解码及Python开销。

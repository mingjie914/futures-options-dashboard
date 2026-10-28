# 本地部署

本地开发时 API 读取 `DASHBOARD_RUNTIME_ROOT` 中的 DuckDB 快照。源 Parquet 仅由离线构建读取，必须显式配置，不自动搜索本机目录。默认仅监听 `127.0.0.1`；未来云端 API 可独立运行，不要求原始数据目录存在。

每日顺序：更新上游本地表、构建新快照、执行校验、发布 `current.json`、检查健康状态。构建失败不会替换当前成功批次。

首次安装和本地启动：

```powershell
Copy-Item .env.example .env
python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -e "backend[test]"
npm ci
# 在 .env 设置实际 SEAT_LOCAL_ROOT 和 MARKET_1D_LOCAL_ROOT，再构建。
backend\.venv\Scripts\python.exe -m seat_dashboard.jobs.build_snapshot --full
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\dev.ps1
```

`--date YYYY-MM-DD` 适合快速开发快照；正式日常使用推荐 `--full`，从而保留席位历史、反转识别与统计验证所需历史窗口。API 请求只读取已发布 DuckDB，不扫描原始 Parquet。

验收命令：

```powershell
backend\.venv\Scripts\python.exe -m seat_dashboard.jobs.verify_snapshot
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\test.ps1
```

移机验证使用 `python -m seat_dashboard.jobs.verify_snapshot --runtime-root var --export-root var/cloud-ready`。仅将导出的数据库和清单作为 API 私有运行数据，配置运行目录后复验；前端只发布 `dist`。本地上传测试产物不代表未来生产长期从本机取数，正式数据来源与广播合同仍待云端方案确定。

## 在线资源与离线构建

在线 API 使用 `/api/seats/v1`（兼容 `/api/v1`），默认 1 个查询槽、DuckDB 每连接 1 线程/256 MB、每进程 128 MiB 响应缓存。配置项见 `.env.example`。这些是应用内限额；systemd Slice/进程数/反代由服务器配置控制，需单独验收总 RSS、OOM、CPU 和冷启动。

构建 job 与 API 是独立命令/进程，API 不启动构建。当前 `--full` 创建新文件并重建维表、事实集市、信号、历史验证与校验；`--date` 生成所选日期输出，但历史窗口仍可能扫描源历史。所谓 raw 增量只指 raw 日期晚于派生表最大日期时补聚合新增日期，**不是跨快照全链增量**，不会处理旧日源端修订/删除。源变更契约未完成前，不改为仅追加、不虚报全增量。

## 显式保留与清理

生产 API 仅需私有查询快照与清单，完整原始 Parquet 不必长期重复驻留在 SG 在线节点；输入的存储/拉取生命周期属于离线构建 job。当前快照 + 上一个可回滚快照 + 最多一个 staging 是目标保留策略，现有本地研究历史不自动删除。

先运行预览：

```sh
python -m seat_dashboard.jobs.prune_snapshots --runtime-root <运行目录>
```

命令只识别 snapshots 下标准命名的旧批次及 staging 下标准临时库，不递归、不触碰原始输入、自定义研究文件或未来批次。默认保留 24 小时内文件并报告 `protected_recent`；这可能暂时超过 staging 目标数量，不代表已达到硬磁盘上限。

维护窗口停止构建/发布任务并等待旧查询结束后，再加 `--apply` 执行；需要取消年龄保护时显式传 `--min-age-hours 0`。发布指针变化时停止清理。旧批次清单/正式回滚操作、构建中临时磁盘上限和云端任务互斥仍需上线编排确认。本轮仅在测试临时目录验证删除，没有对现有研究/生产运行目录执行清理。

## 上线前发布一致性清单（2026-10-28 核对）

| 环节 | 当前证据 | 上线待办 |
| --- | --- | --- |
| 快照切换 | 新 DuckDB 先进入版本路径，再以 `os.replace` 切换 `current.json`；不会先移走正在服务的旧库 | 保证同文件系统、单发布者与不复用批次；当前目标同名时仍可被覆盖，缺少发布锁。对本项目发布器补进程骤停故障注入；尚无断电持久性保证 |
| 单请求读取 | `read_session` 固定清单、绝对数据库路径与版本；SQL 和响应元数据使用同一版本，已有切换测试 | 固定路径不等于持有读者租约；从读取清单到打开库，以及等待查询槽期间，必须保护被引用版本 |
| 整屏读取 | 前端比较各接口 `data_version`，不一致重试一次，持续不一致则报错；席位历史也检查版本 | 这是防混版校验，并非服务端整屏版本固定。若要求切换期间仍可连续读完旧整屏，需版本选择契约及对应保留期 |
| 旧版 GC | 保留当前及前一版，默认 24 小时年龄保护；删除前复查清单 | 没有跨进程活跃读者追踪，年龄保护不能证明读者已释放。上线前禁止在线自动 `--apply`；维护窗口需停止新请求、排空旧请求及构建/发布后清理，或实现读者租约与发布/GC 互斥。清单复查与删除之间仍有竞争窗口 |
| 离线输入 | 当前构建器多次读取 Parquet 数据集路径 | RDS 消费适配应先形成完整不可变输入 release，整个构建固定一个 release；不得将逐月替换中的目录直接作为输入。旧版输入同样在构建读者释放后回收 |

云端补充实验来自 `E:/云服务器/甲骨文/核验-20261027/sg-rds-consumer-acceptance-20261028.json`：两个月日线取数、变化比较、候选原子指针进程骤停与固定旧读者检查通过。它不是本项目席位 RDS 适配、完整构建、断电或生产验收证据；现有发布器不能据此标为全部通过。以上仅记录上线待办，不改变业务指标、研究数据或生产部署。

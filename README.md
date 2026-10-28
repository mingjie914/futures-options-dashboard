# Futures & Options Dashboard · 统一研究终端

2026-09-20：期货业务源码已纳入本仓库的 `futures/`，不再依赖同级旧工程。保留独立API进程，复用既有前端生成与固定后端发布流程；生成副本不进入Git。源码仅在本仓库维护，旧目录保留本机历史资料与兼容启动入口。

## 首次克隆与准备

跨机器路径、无数据启动及云端挂载约定见 [跨机器运行与数据依赖](docs/跨机器运行与数据依赖.md)。应用运行与 Wind 采集分离；不要求安装 Wind 才能启动。

本项目为行情与统计研究看板，默认只监听127.0.0.1，没有下单或自动采集。Git不包含Wind/CEIC行情、用户自选、密钥、运行缓存及构建产物。无行情时显示未配置数据，不能用示例行情冒充真实数据。

依赖：Python 3.9+（work机历史验证3.9，本机验证3.12）、Node.js 20.19+或22.12+、pnpm。Windows PowerShell中执行：

```powershell
python -m venv .venv
python -m venv .venv-futures
./.venv/Scripts/python.exe -m pip install -r requirements.lock.txt
./.venv-futures/Scripts/python.exe -m pip install -r requirements.futures.lock.txt
./.venv/Scripts/python.exe tools/freeze_backend.py
Set-Location frontend
pnpm install --frozen-lockfile
pnpm build
Set-Location ..
./start.ps1
```

期货日线通过 `FUTURES_DATA_ROOT` 指向自己的Parquet数据目录；Wind采集解释器通过 `OPTIONS_WIND_PYTHON` 指定，并在网页显式手动刷新。基本面研究原件放在 `futures/runtime/`，需要发布时再运行 `tools/publish_futures.ps1`。无私有数据也可以启动并运行合成数据的单元测试；两项真实行情验收缺归档时明确跳过。

Linux可使用虚拟环境的 `bin/python` 路径运行 `start.py`，但本轮没有完成Linux实机验收；Wind采集仍需有相应授权及可用环境。公网部署前按保存的部署评估另行完成权限、数据授权和运行保障。

## 源码与测试

- `backend/`、`frontend/src/`：期权业务与统一入口。
- `futures/backend/`、`futures/frontend/src/`、`futures/tools/`：唯一的期货源代码；不要编辑生成的 `frontend/src/futures/`。
- `frontend/tools/build_frontend.py`：构建时按来源哈希复用或生成期货模块，无需维护第二份可编辑代码。
- 期权及整合测试：根目录运行 `./.venv/Scripts/python.exe -m pytest tests -q`。
- 期货测试：进入 `futures/`，运行 `../.venv-futures/Scripts/python.exe -m pytest tests -q`。两模块包名保留原状，应分进程测试。
- 前端逻辑：首次先在 `futures/frontend/` 执行 `pnpm install --frozen-lockfile` 和 `pnpm build`（准备原有独立构建验收产物），然后从根目录运行 `node --test futures/frontend/tests/*.test.mjs`；统一前端在 `frontend/` 执行 `pnpm build`。

源码同步发布至用户指定的GitHub和Gitee仓库；两个远端使用同一提交，不强制覆盖远端历史。

2026-09-19：期货目录与本目录默认启动均进入同一套统一终端；已同步最新已验收的期货前后端及已保存审阅数据。后台保留独立进程，期权仍使用 Wind 手动行情截面。

**后续接续先读：[部署与统一维护入口](docs/部署与统一维护入口.md)。** 完整云部署评估、阶段状态、发布命令和回退位置都从该文档进入，避免依赖对话记忆。

## 启动与使用

在本目录 PowerShell 执行 `./start.ps1`，打开 http://127.0.0.1:8790/options/ 。从期货目录运行 `start.py` 则指向同一终端的 http://127.0.0.1:8790/futures/ 。顶部切换两个模块；重复启动复用健康的现有服务，不再开启第二套终端。Ctrl+C 停止本次启动的服务；不属于本终端的端口占用仍会退出，不强行停止进程。

- 期权：IV 历史与分位、波动率微笑与期限结构、持仓成交与 PCR、期权链。展示范围取决于已有快照；页面刷新只读，显式手动取数需要配置采集环境。
- 期货：保留现有历史研究页面，读取原有本地历史数据，运行文件及工作台副本在本项目隔离保存。
- 退出分析：价格×时间损益热图、目标到达时退出、盈利目标反查；交易成本为可选的往返合计估算，支持普通/保守退出对照，单独回看本地已保存截面。
- 工作台原有情景默认不含额外手续费；退出分析页使用该页费用设置。跨到期处置模型尚未开放。盘中量仓使用 Wind 实时字段；增减对比前一交易日日线持仓。缺失字段保留为空。

`.venv` 为入口/期权环境，`.venv-futures` 为冻结期货环境；也可通过 `OPTIONS_PYTHON`、`FUTURES_PYTHON` 显式指定解释器。Wind 手动采集仅在配置 `OPTIONS_WIND_PYTHON` 后启用。服务仅绑定本机，入口端口 8790，期权 8794，期货 8795。

页面仅保留行情与统计，不提供模拟交易、CSV 下载或打印。期货 dashboard 按当前栏目计算并返回所需数据；界面筛选在刷新后保留。历史研究文档中的旧页面不代表当前功能。

## 维护与验收

- 前端构建：在 `frontend` 执行 `pnpm build`，按仓库内期货源码哈希校验/生成副本；构建不会调用Wind或更新研究数据。
- 更新期货统一发布版本：正常停止统一终端后，在本目录执行 `./tools/publish_futures.ps1`，一起更新前端、后端和已保存审阅数据，再启动验收。数据按文件哈希核对，旧审阅数据保留回退副本，不覆盖自选/研究计划数据库；不在使用中替换运行版本。
- 仅发布代码或没有基本面原件时：正常停止统一终端，在 `frontend/` 执行 `pnpm build`，再回根目录执行 `./.venv/Scripts/python.exe tools/freeze_backend.py`。不加 `--refresh-data`，保留现有展示数据；上述完整发布脚本要求 `futures/runtime/fundamental_lab` 和 `futures/runtime/ceic_lab` 均已准备好。
- 期货 `start.py --standalone` 仅用于显式开发调试，8766 不再是日常查看入口。期货源码修改通过本模块验收后，须发布到统一终端再交付；不能只验收独立旧页面。
- 数值与接口检查：`.venv/Scripts/python.exe -m pytest tests -q`。
- 原始行情在 `data/raw`，当前数据指针和服务日志在 `runtime`。旧分析复制在 `data/raw/legacy`，原文件保留。

## 范围与设计文档

- [部署与统一维护入口](docs/部署与统一维护入口.md)：长期接续入口、当前完成状态及剩余实施步骤。
- [完整云部署评估](docs/统一维护与云部署评估-20260914.md)：保存2026-09-14的数据规模、性能测量、云服务选型、域名备案与数据权限评估；历史测量不冒充最新状态。
- [退出分析与历史快照](docs/退出分析与历史快照-20260912.md)：已移除页面的历史设计与验证记录。
- [9月12日审阅优化实施记录](docs/审阅优化实施记录-20260912.md)：到期计算修复、时间基准、风险预算比较、跨月波动率、持仓异动及刷新质量验收。
- [首版实施范围与进展](docs/首版实施范围与进展.md)：当前实现、简化决策及未开放范围。
- [首版验收记录](docs/首版验收记录.md)：实际验证及限制。
- [统一终端架构与冲突预案 v0.4](docs/统一终端架构与冲突预案-v0.4.md)。
- [期权实施方案 v0.3](docs/期权终端实施方案-v0.3.md)。
- [后续实施任务清单](docs/实施任务与验收清单-v0.3.md)：完整路线，不代表首版全部完成。

IO/HO/AU、持续订阅、自动策略排名、完整跨月矩阵、长期 IV 历史及云部署留待后续。本轮没有创建自动调度或公开站点。

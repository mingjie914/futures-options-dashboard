# 研究看板 · MINGJIE

席位、期货、期权是同一前端下的平级模块。源码在本仓库维护，原席位工程保留作迁移前参考；本机目录名“期权终端”仅为兼容现有路径。

## 运行

准备 Python 3.11+、Node 22.12+ 和 pnpm，在项目根目录执行：

```powershell
python -m venv .venv
python -m venv .venv-futures
python -m venv .venv-seats
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv-futures/Scripts/python.exe -m pip install -r futures/requirements.txt
./.venv-seats/Scripts/python.exe -m pip install -r seats/backend/requirements.lock -e seats/backend
cd frontend
pnpm install --frozen-lockfile
pnpm build
cd ..
./.venv/Scripts/python.exe tools/freeze_backend.py --frontend
./start.ps1 --module seats
```

入口 http://127.0.0.1:8790/ ，顶部切换席位、期货、期权(Beta)。默认打开期货；支持 `/seats/`、`/futures/`、`/options/` 深链接。已运行时复用服务，Ctrl+C 只停止本次启动的进程。

页面只展示已有行情与统计，不采集数据，不提供模拟交易、CSV 下载或打印。无数据时明确显示缺口，不填模拟值。数据挂载见 [跨机器配置](docs/跨机器运行与数据依赖.md)；席位使用 `SEATS_RUNTIME` 指向含 `manifests/current.json` 和所引用 DuckDB 的目录，默认 `runtime/seats`。不要仅复制数据库而遗漏清单。

## 维护

| 内容 | 唯一源码 | API / 本地端口 |
|---|---|---|
| 共享导航、字体、请求缓存 | `frontend/src/Shell.jsx`、`shell.css`、`transport.js` | 网关 8790 |
| 席位 | `frontend/src/seats`、`seats/backend/src` | `/api/seats/v1` / 8796 |
| 期货 | `futures/frontend/src`、`futures/backend` | `/api/futures/v1` / 8795 |
| 期权 | `frontend/src/Options.jsx` 等、`backend` | `/api/options/v1` / 8794 |

`frontend/src/futures` 是构建生成副本，不编辑、不提交。席位采用局部 CSS，三模块与图表引擎按需加载，只保留一份 React 和字体。

席位分四页：席位动向与市场信号看全市场；品种持仓看单品种会员排名和历史统计；跨期持仓看同品种不同合约。后两页共用品种。现有席位接口通过 `detail=groups/positions/contracts` 按页返回，省略参数保留完整响应；沿既有 code/web 流程同版发布与回滚。

页脚由主页的 `site-footer.js` 统一维护，看板仅加载该静态组件，不复制文案或样式；`VITE_HOME_URL` 同时决定主页链接和页脚来源。主页发布页脚更新后，看板下次打开或刷新即可同步，无需重新构建看板。

正常停止服务后，执行 `./tools/publish_futures.ps1 -CodeOnly` 构建完整前端并冻结期货后端，保留全部运行数据。旧命令名为兼容保留；不加 `-CodeOnly` 才复制已保存的期货基本面审阅数据。发布和回滚边界见 [部署与统一维护入口](docs/部署与统一维护入口.md)。

## 验证

- 根目录：`.venv/Scripts/python.exe -m pytest tests -q`
- `futures/`：`../.venv-futures/Scripts/python.exe -m pytest tests -q`
- `seats/backend/`：`../../.venv-seats/Scripts/python.exe -m pytest -q`
- `frontend/`：`pnpm typecheck`、`pnpm test`、`pnpm build`
- 统一入口验证三模块切换、筛选恢复、图表与窄屏；不能只验证独立开发页。

截至 2026-10-29，[期货](https://mingjiec.com/futures/)与[席位](https://mingjiec.com/seats/)已通过 SG Caddy/systemd 上线，主页入口已接入；期权页面暂未接入数据。生产使用 RDS 数据，主力历史缺口与修订溯源缺失仍明确展示。已启用窗口刷新、分区差量交付、席位日度表增量替换及必要历史指标重算，无变更跳过。自然变更批次尚未到来，生产复核为健康 NOOP。当前部署与验收边界见[维护入口](docs/部署与统一维护入口.md)，旧日期记录仅作历史证据。


统一前端回归在 `frontend` 中执行 `pnpm build`、`pnpm typecheck`、`pnpm test`，只安装该目录依赖。期货源码和计算测试由统一构建复用；旧独立 Sites 构建壳已退役。

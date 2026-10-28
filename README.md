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

正常停止服务后，执行 `./tools/publish_futures.ps1 -CodeOnly` 构建完整前端并冻结期货后端，保留全部运行数据。旧命令名为兼容保留；不加 `-CodeOnly` 才复制已保存的期货基本面审阅数据。发布和回滚边界见 [部署与统一维护入口](docs/部署与统一维护入口.md)。

## 验证

- 根目录：`.venv/Scripts/python.exe -m pytest tests -q`
- `futures/`：`../.venv-futures/Scripts/python.exe -m pytest tests -q`
- `seats/backend/`：`../../.venv-seats/Scripts/python.exe -m pytest -q`
- `frontend/`：`pnpm typecheck`、`pnpm test`、`pnpm build`
- 统一入口验证三模块切换、筛选恢复、图表与窄屏；不能只验证独立开发页。

本地默认绑定 loopback。生产认证、Caddy/DNS、数据增量发布与云端资源验收独立推进，合并不代表已部署。旧日期文档只保留历史设计与证据，不代表当前页面或数据状态。


统一前端回归在 `frontend` 中先执行 `pnpm build`、`pnpm typecheck`，再执行 `pnpm test`，只安装该目录依赖。旧独立 Sites 打包测试不属于 SG 发布验收；仅主动维护旧 Sites 产物时，在 `futures/frontend` 单独执行原 `build` 与 `test:sites`，正常研究看板无需安装第二套前端依赖。

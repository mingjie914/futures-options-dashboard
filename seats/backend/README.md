# 期货席位行为监控后端

这是一个读取版本化 DuckDB 快照的 FastAPI 后端，提供 REST 风格 HTTP 查询。构建端和运行端可位于不同机器；API 不需要原始 Parquet 或本机研究目录。RDS 查询和发布实现仍待完成，不能直接启用。

## 运行入口

从研究看板根目录按 [README](../../README.md) 安装独立 `.venv-seats` 并执行 `start.ps1 --module seats`；日常不再单独启动旧前端。源码在本目录，席位前端位于 `frontend/src/seats`。离线构建使用下文 Python 模块命令，不在 API 启动时构建。

默认运行时目录为项目根目录下的 `var`。可以通过环境变量覆盖：

```text
SEAT_LOCAL_ROOT=data/futuresseatdata
MARKET_1D_LOCAL_ROOT=data/futureshistoryprices1d
DASHBOARD_RUNTIME_ROOT=var
DASHBOARD_READ_STORE=duckdb
```

相对配置以项目根目录解析，不自动搜索兄弟目录。构建前显式配置输入路径；云端 API 仅配置 `DASHBOARD_RUNTIME_ROOT` 为挂载快照的绝对路径，无需设置两个输入目录。没有快照时返回不可用，不自动生成模拟数据。

运行依赖只保留 DuckDB、FastAPI、Uvicorn 和 python-dotenv。PyArrow 属于测试额外依赖，PyMySQL 属于可选 `mysql` 依赖；安装驱动不代表 MySQL 页面实现已完成。构建 SQL 随 Python 包一起分发。

使用 `python -m seat_dashboard.jobs.verify_snapshot --runtime-root var --export-root <新目录>` 导出验证后的当前快照；导出目录包含相对路径清单和 SHA-256，搬迁后再次执行 `verify_snapshot --runtime-root <目录>`。快照应在 API 私有存储中，不能放到前端静态资源目录。

## 数据流

```text
ParquetSource -> SnapshotBuilder -> DuckDBPublisher -> current.json -> DuckDBReader -> FastAPI
```

API 不读取原始 Parquet。快照构建发生错误时不替换 `current.json`，旧快照仍可回滚使用。

## 命令

```powershell
./scripts/build_snapshot.ps1 -Full
./scripts/build_snapshot.ps1 -Date 2026-07-28
./scripts/build_snapshot.ps1 -Full -DryRun
./scripts/verify_snapshot.ps1
```

`-Date` 是单日构建模式，适合开发和同日修订；`-Full` 构建所有可用日期并为最新日期生成统计结果。

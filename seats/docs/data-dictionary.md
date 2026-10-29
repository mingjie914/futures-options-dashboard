# 期货席位行为监控数据字典

## 本地源数据

源目录只读，不由看板代码修改。

| 逻辑名称 | 本地目录 | 粒度 | 主要用途 |
|---|---|---|---|
| `product_daily` | `futuresseatdata/derive_product_daily` | 交易日 × 品种 | 品种多空总量、席位数和变化 |
| `product_seat` | `futuresseatdata/derive_product_seat` | 交易日 × 品种 × 席位 | 席位排名、CR5、席位信号 |
| `main_contract_seat` | `futuresseatdata/derive_main_contract_seat` | 交易日 × 主力合约 × 席位 | 主力合约席位、收盘价、持仓量 |
| `raw_seat` | `futuresseatdata/raw_seat_position` | 交易日 × 合约 × 席位 | 合约汇总和跨期信号 |
| `market_1d` | `futureshistoryprices1d` | 交易日 × 品种/合约 | 历史收益验证和价格背景 |

当前已核对的源字段包括：

- 席位：`trade_date`、`exchange`、`root`、`product_code`、`contract_code`、`symbol`、`seat_name`、多空持仓和变动。
- 品种日：`total_long`、`total_short`、`net_position`、`long_change`、`short_change`、`seat_count`。
- 主力席位：上述席位字段及 `close`、`open_interest`。
- 行情日线：`exchange`、`symbol`、`trade_date`、OHLC、`volume`、`position`、`sequence`。

## 看板逻辑表

| 表 | 颗粒度 | 说明 |
|---|---|---|
| `seat_dash_product_dim` | 品种 | 品种代码、交易所和展示名 |
| `seat_dash_contract_dim` | 合约 | 合约月份和排序来源 |
| `seat_dash_seat_dim` | 标准席位 | 使用 `md5(trim(seat_name))` 作为跨后端稳定 ID |
| `seat_dash_seat_alias` | 原始席位名 | 原始名称到标准席位的可追溯映射 |
| `seat_dash_tag_definition` | 标签 | 静态和动态标签字典 |
| `seat_dash_seat_tag_assignment` | 席位标签分配 | 来源、有效期、置信度和证据 |
| `seat_dash_combination` | 组合 | 系统组合或用户组合定义 |
| `seat_dash_combination_member` | 组合成员 | 组合和席位的关系 |
| `seat_dash_market_daily` | 交易日 × 合约 | 由行情 Parquet 规范化的价格背景 |
| `seat_dash_product_daily` | 交易日 × 品种 | 看板核心指标 |
| `seat_dash_product_seat_daily` | 交易日 × 品种 × 席位 | 席位榜单和组合基础事实 |
| `seat_dash_main_contract_seat_daily` | 交易日 × 主力合约 × 席位 | 主力合约席位事实 |
| `seat_dash_contract_daily` | 交易日 × 合约 | 跨期结构和单腿总量 |
| `seat_dash_contract_seat_daily` | 交易日 × 合约 × 席位 | 跨期反向筛选和完整合约腿 |
| `seat_dash_combination_signal_daily` | 交易日 × 品种 × 组合 | 全成员同向的组合一致性及动作 |
| `seat_dash_seat_signal_daily` | 交易日 × 品种 × 席位 × 信号 | 反转和持仓异动 |
| `seat_dash_term_signal_daily` | 交易日 × 品种 × 近远月 × 席位 | 近远月反向信号 |
| `seat_dash_rule_result_daily` | 交易日 × 品种 × 规则 × 周期 | 历史收益、胜率和置信区间 |
| `seat_dash_etl_run` | 批次 | 来源、行数、校验和状态 |
| `seat_dash_publish_state` | 数据集 | 当前公开批次 |

## 统一约定

- `product_code` 进入看板后统一转为大写形式，例如 `RB.SHFE`。
- `seat_id` 为席位名称去首尾空格后的 MD5，跨批次保持稳定。
- 所有批次结果携带 `batch_id`。
- `data_backend=local` 表示 DuckDB 服务快照；`data_backend=demo` 只允许出现在显式 demo fixture。
- `f2_value = net_position / (total_long + total_short)`；仅在分母有效时 `f2_status=ready`。
- `net_cr5` 使用绝对净持仓前 5 名占全部席位绝对净持仓的比例。
- `previous_net_position` 和 `net_position_change` 基于同品种同席位上一可用交易日；新出现与退出状态由 `entry_state` 标识。
- 当前分位、规律库截点和 5/10 日结果均写入 `seat_dash_rule_result_daily`，不在浏览器临时回算。
- `evidence_json` 是可审计证据，不是自然语言事实声明。

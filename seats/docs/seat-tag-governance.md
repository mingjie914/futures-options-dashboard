# 席位标签治理

## 标签类别

- 静态/人工：资金席位、套保盘、弱势席位、重点跟踪、疑似套利、自定义标签。
- 动态规则：多空反转、多头激增/减仓、空头激增/减仓、近远月反向。

## 必备证据

每项标签必须保存来源、适用品种、有效起止日期、置信度、证据、规则版本和备注。人工标签不因名称相似自动传播；动态推断必须保留“疑似”等限定语。

## 席位分组口径（2026-10-28）

`config/combinations.json` 配置产业大户（6）、券商自营（6）、投机交易（3）、外资席位（3）、家人席位（4）。共 22 个成员位置、18 个不同席位；跨组重叠分别计入，不作为独立投票或可直接相加的资金类别。这些名称是研究分组，不代表账户实际业务身份。东方财富期货使用数据源规范名“东方财富”。

外资组为高盛期货、摩根大通、瑞银期货。[高盛官方更名公告](https://www.goldmansachs.cn/futures/)确认乾坤更名为高盛；分组计算通过现有 `seat_dash_seat_alias` 接续历史乾坤记录，原始事实名称和单席历史不改写。新旧名称不得作为两个成员；同日同品种同时出现两份记录时中止构建，避免未经核实相加。别名规则参与 `combination_version` 指纹。

“席位分组”矩阵展示所选交易日所有有品种汇总数据的品种，各组净额、净变化分别对已披露成员求和。首行显示净额与净变化，次行显示净多、净空、净零与缺席人数；全缺时金额为 null，不按零补齐。完整组净多用红底、净空用绿底，全员同向时加深；不完整组用中性底色。默认按完整组的最大净变化绝对值排序，不完整组不参与该幅度；可切换全员同向组数排序。跨品种手数不是资金规模，跨组重叠不能直接加总。

全员有数据、净持仓同号且非零时才突出“全员同向”；不再以此过滤矩阵。背离依然使用原严格信号：所有成员净变化也须同号、非零，才形成加多、减多、加空或减空动作。矩阵混合或不完整的合计不用于放宽背离判定。零信号是合法结果。

背离沿用原四类反向动作规则，比较对象更新为“投机交易 × 家人席位”。投机交易沿用 `capital_strike`（成员不变）；家人席位沿用 `wolong_fengchu`，从东方财富、徽商扩充为四席。配置指纹写入 `combination_version`，避免把跨版本结果混为同一口径。单席标签与此分组分别维护，不联动改写。

旧配置：`config/backups/combinations-20261028-eight-groups.json`。本次改动前工作区备份：`var/backups/before-five-groups-20261028-155357/`。原 `var/cloud-ready-b14` 快照不变，试验版在 `var/grouping-five-preview`；本地预览 API 指向后者。回看旧版只需将 API 的 `DASHBOARD_RUNTIME_ROOT` 改回前者并重启；如恢复后续构建口径，再恢复备份的组合配置。

无需访问原本机数据目录或 RDS，可复制已发布快照，仅重算组合表及信号：

```powershell
backend/.venv/Scripts/python.exe -m seat_dashboard.jobs.build_snapshot --reclassify-from var/cloud-ready-b14 --runtime-root var/grouping-five-preview
backend/.venv/Scripts/python.exe -m seat_dashboard.jobs.verify_snapshot --runtime-root var/grouping-five-preview
```

本次 2026-10-26 同日对照：旧八组 71 条组信号/35 个品种，新五组 26 条/22 个品种；背离从玻璃 1 条变为 0 条。新增家人成员中平安净空、方正净多，打破旧组的全员同向及同动作条件。15 张非组合业务表行数和内容哈希保持一致，独立逐组复算通过。核验记录：`var/five-groups-check.json`。这次试验没有重新获取行情或发布到生产。

## 配置入口

2026-10-28 更名修正生成 b16（b15 保留），2026-10-26 外资完整覆盖 28/73 品种、同向信号 9 条，总组信号 35 条。8 月 13/14 日与最新日的 221 个外资单元格独立复算通过，14 张非组合业务表内容校验一致，其余四组信号值不变。核验：`var/foreign-rename-check.json`；旧指针备份：`var/backups/before-foreign-alias-b15.json`。

人工标签使用项目内 `config/seat-tags.csv`，一行表示一个有时效的标签分配：

```csv
seat_name,tag_code,product_code,valid_from,valid_to,source,confidence,evidence,note
某期货席位,capital_seat,RB.SHFE,2026-01-01,2026-12-31,内部审批台账,0.90,经审批的资金席位分类,示例格式
```

可用静态代码为 `capital_seat`、`hedging_seat`、`weak_seat`、`tracked_seat`、`suspected_arbitrage`。`product_code` 留空表示全品种。当前配置根据参考图录入 10 项分配：资金席位 4 项、弱势席位 1 项、套保盘 2 项、RB 当日疑似套利 3 项。

构建器对未知席位、未知品种、缺少证据、置信度越界、日期倒置和重复分配全部失败关闭；只有快照成功发布后标签才会进入 API。参考图复原标签属于研究台账，不是交易所或席位的官方身份认定。

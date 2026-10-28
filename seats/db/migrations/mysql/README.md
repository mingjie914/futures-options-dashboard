# MySQL/RDS migrations

`001_create_seat_dashboard.sql` 只创建 `seat_dash_*` 看板数据集市，不修改现有原始席位表或行情表。执行前应在非生产库验证 MySQL 版本、RDS 分区限制和字符集。

V1 不自动执行此目录的迁移，也不从 RDS 读取。迁移阶段先影子写入并做 DuckDB/MySQL 结果一致性检查，再切换 `DASHBOARD_READ_STORE=mysql`。

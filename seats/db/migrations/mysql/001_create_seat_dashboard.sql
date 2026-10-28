-- Local-first dashboard mart schema.
-- This migration creates only seat_dash_* tables and does not alter futuresseatraw.

CREATE TABLE IF NOT EXISTS seat_dash_publish_state (
    dataset_name VARCHAR(64) NOT NULL,
    batch_id BIGINT NOT NULL,
    trade_date DATE NULL,
    published_at DATETIME(6) NOT NULL,
    data_backend VARCHAR(16) NOT NULL,
    complete TINYINT(1) NOT NULL,
    metadata_json LONGTEXT NOT NULL,
    PRIMARY KEY (dataset_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS seat_dash_etl_run (
    batch_id BIGINT NOT NULL,
    started_at DATETIME(6) NOT NULL,
    finished_at DATETIME(6) NULL,
    status VARCHAR(32) NOT NULL,
    mode VARCHAR(16) NOT NULL,
    source_manifest_sha256 CHAR(64) NULL,
    date_min DATE NULL,
    date_max DATE NULL,
    row_counts_json LONGTEXT NOT NULL,
    validation_json LONGTEXT NOT NULL,
    error_message TEXT NULL,
    PRIMARY KEY (batch_id),
    KEY ix_etl_status (status, finished_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS seat_dash_product_dim (
    product_code VARCHAR(64) NOT NULL,
    root VARCHAR(32) NOT NULL,
    exchange VARCHAR(32) NOT NULL,
    display_name VARCHAR(128) NOT NULL,
    active TINYINT(1) NOT NULL,
    PRIMARY KEY (product_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS seat_dash_contract_dim (
    contract_code VARCHAR(96) NOT NULL,
    product_code VARCHAR(64) NOT NULL,
    exchange VARCHAR(32) NOT NULL,
    symbol VARCHAR(64) NULL,
    contract_month INT NULL,
    contract_order_source VARCHAR(64) NOT NULL,
    PRIMARY KEY (contract_code),
    KEY ix_contract_product (product_code, contract_month)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS seat_dash_seat_dim (
    seat_id CHAR(32) NOT NULL,
    seat_name VARCHAR(128) NOT NULL,
    active TINYINT(1) NOT NULL,
    PRIMARY KEY (seat_id),
    UNIQUE KEY uq_seat_name (seat_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS seat_dash_seat_alias (
    source_seat_name VARCHAR(128) NOT NULL,
    seat_id CHAR(32) NOT NULL,
    source VARCHAR(64) NOT NULL,
    note VARCHAR(255) NULL,
    PRIMARY KEY (source_seat_name),
    KEY ix_alias_seat (seat_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS seat_dash_tag_definition (
    tag_code VARCHAR(64) NOT NULL,
    label VARCHAR(64) NOT NULL,
    category VARCHAR(32) NOT NULL,
    color_token VARCHAR(32) NOT NULL,
    description VARCHAR(255) NOT NULL,
    is_dynamic TINYINT(1) NOT NULL,
    PRIMARY KEY (tag_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS seat_dash_seat_tag_assignment (
    assignment_id CHAR(32) NOT NULL,
    seat_id CHAR(32) NOT NULL,
    tag_code VARCHAR(64) NOT NULL,
    product_code VARCHAR(64) NULL,
    valid_from DATE NULL,
    valid_to DATE NULL,
    source VARCHAR(64) NOT NULL,
    confidence DOUBLE NULL,
    evidence_json LONGTEXT NOT NULL,
    note VARCHAR(255) NULL,
    PRIMARY KEY (assignment_id),
    KEY ix_assignment_lookup (seat_id, product_code, valid_from, valid_to)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS seat_dash_combination (
    combination_id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    scope VARCHAR(32) NOT NULL,
    is_system TINYINT(1) NOT NULL,
    active TINYINT(1) NOT NULL,
    PRIMARY KEY (combination_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS seat_dash_combination_member (
    combination_id VARCHAR(64) NOT NULL,
    seat_id CHAR(32) NOT NULL,
    PRIMARY KEY (combination_id, seat_id),
    KEY ix_combination_member_seat (seat_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS seat_dash_market_daily (
    trade_date DATE NOT NULL,
    product_code VARCHAR(64) NOT NULL,
    exchange VARCHAR(32) NOT NULL,
    symbol VARCHAR(64) NOT NULL,
    close DOUBLE NULL,
    open_interest BIGINT NULL,
    volume BIGINT NULL,
    sequence INT NULL,
    batch_id BIGINT NOT NULL,
    PRIMARY KEY (trade_date, symbol, exchange),
    KEY ix_market_product_date (product_code, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
PARTITION BY RANGE COLUMNS (trade_date) (
    PARTITION p2020 VALUES LESS THAN ('2021-01-01'),
    PARTITION p2021 VALUES LESS THAN ('2022-01-01'),
    PARTITION p2022 VALUES LESS THAN ('2023-01-01'),
    PARTITION p2023 VALUES LESS THAN ('2024-01-01'),
    PARTITION p2024 VALUES LESS THAN ('2025-01-01'),
    PARTITION p2025 VALUES LESS THAN ('2026-01-01'),
    PARTITION p2026 VALUES LESS THAN ('2027-01-01'),
    PARTITION pmax VALUES LESS THAN (MAXVALUE)
);

CREATE TABLE IF NOT EXISTS seat_dash_product_daily (
    trade_date DATE NOT NULL,
    product_code VARCHAR(64) NOT NULL,
    root VARCHAR(32) NOT NULL,
    exchange VARCHAR(32) NOT NULL,
    total_long BIGINT NOT NULL,
    total_short BIGINT NOT NULL,
    net_position BIGINT NOT NULL,
    long_change BIGINT NOT NULL,
    short_change BIGINT NOT NULL,
    net_change BIGINT NOT NULL,
    seat_count BIGINT NOT NULL,
    long_short_ratio DOUBLE NULL,
    long_cr5 DOUBLE NULL,
    short_cr5 DOUBLE NULL,
    net_cr5 DOUBLE NULL,
    divergence_state VARCHAR(32) NOT NULL,
    f2_value DOUBLE NULL,
    f2_status VARCHAR(32) NOT NULL,
    batch_id BIGINT NOT NULL,
    PRIMARY KEY (trade_date, product_code),
    KEY ix_product_daily_code (product_code, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
PARTITION BY RANGE COLUMNS (trade_date) (
    PARTITION p2020 VALUES LESS THAN ('2021-01-01'),
    PARTITION p2021 VALUES LESS THAN ('2022-01-01'),
    PARTITION p2022 VALUES LESS THAN ('2023-01-01'),
    PARTITION p2023 VALUES LESS THAN ('2024-01-01'),
    PARTITION p2024 VALUES LESS THAN ('2025-01-01'),
    PARTITION p2025 VALUES LESS THAN ('2026-01-01'),
    PARTITION p2026 VALUES LESS THAN ('2027-01-01'),
    PARTITION pmax VALUES LESS THAN (MAXVALUE)
);

CREATE TABLE IF NOT EXISTS seat_dash_product_seat_daily (
    trade_date DATE NOT NULL,
    product_code VARCHAR(64) NOT NULL,
    seat_id CHAR(32) NOT NULL,
    seat_name VARCHAR(128) NOT NULL,
    long_position BIGINT NOT NULL,
    long_change BIGINT NOT NULL,
    short_position BIGINT NOT NULL,
    short_change BIGINT NOT NULL,
    net_position BIGINT NOT NULL,
    net_change BIGINT NOT NULL,
    abs_net_position BIGINT NOT NULL,
    abs_net_change BIGINT NOT NULL,
    previous_net_position BIGINT NULL,
    net_position_change BIGINT NOT NULL,
    entry_state VARCHAR(16) NOT NULL,
    contract_count BIGINT NOT NULL,
    batch_id BIGINT NOT NULL,
    PRIMARY KEY (trade_date, product_code, seat_id),
    KEY ix_product_seat_rank (product_code, trade_date, abs_net_position),
    KEY ix_product_seat_name (seat_id, trade_date, product_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
PARTITION BY RANGE COLUMNS (trade_date) (
    PARTITION p2020 VALUES LESS THAN ('2021-01-01'),
    PARTITION p2021 VALUES LESS THAN ('2022-01-01'),
    PARTITION p2022 VALUES LESS THAN ('2023-01-01'),
    PARTITION p2023 VALUES LESS THAN ('2024-01-01'),
    PARTITION p2024 VALUES LESS THAN ('2025-01-01'),
    PARTITION p2025 VALUES LESS THAN ('2026-01-01'),
    PARTITION p2026 VALUES LESS THAN ('2027-01-01'),
    PARTITION pmax VALUES LESS THAN (MAXVALUE)
);

CREATE TABLE IF NOT EXISTS seat_dash_main_contract_seat_daily (
    trade_date DATE NOT NULL,
    product_code VARCHAR(64) NOT NULL,
    contract_code VARCHAR(96) NOT NULL,
    symbol VARCHAR(64) NOT NULL,
    seat_id CHAR(32) NOT NULL,
    seat_name VARCHAR(128) NOT NULL,
    long_position BIGINT NOT NULL,
    long_change BIGINT NOT NULL,
    short_position BIGINT NOT NULL,
    short_change BIGINT NOT NULL,
    net_position BIGINT NOT NULL,
    net_change BIGINT NOT NULL,
    close DOUBLE NULL,
    open_interest BIGINT NULL,
    batch_id BIGINT NOT NULL,
    PRIMARY KEY (trade_date, contract_code, seat_id),
    KEY ix_main_product_date (product_code, trade_date),
    KEY ix_main_seat_date (seat_id, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
PARTITION BY RANGE COLUMNS (trade_date) (
    PARTITION p2020 VALUES LESS THAN ('2021-01-01'),
    PARTITION p2021 VALUES LESS THAN ('2022-01-01'),
    PARTITION p2022 VALUES LESS THAN ('2023-01-01'),
    PARTITION p2023 VALUES LESS THAN ('2024-01-01'),
    PARTITION p2024 VALUES LESS THAN ('2025-01-01'),
    PARTITION p2025 VALUES LESS THAN ('2026-01-01'),
    PARTITION p2026 VALUES LESS THAN ('2027-01-01'),
    PARTITION pmax VALUES LESS THAN (MAXVALUE)
);

CREATE TABLE IF NOT EXISTS seat_dash_contract_daily (
    trade_date DATE NOT NULL,
    product_code VARCHAR(64) NOT NULL,
    contract_code VARCHAR(96) NOT NULL,
    symbol VARCHAR(64) NOT NULL,
    contract_month INT NULL,
    contract_rank INT NOT NULL,
    total_long BIGINT NOT NULL,
    total_short BIGINT NOT NULL,
    net_position BIGINT NOT NULL,
    seat_count BIGINT NOT NULL,
    batch_id BIGINT NOT NULL,
    PRIMARY KEY (trade_date, contract_code),
    KEY ix_contract_daily_product (product_code, trade_date, contract_rank)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
PARTITION BY RANGE COLUMNS (trade_date) (
    PARTITION p2020 VALUES LESS THAN ('2021-01-01'),
    PARTITION p2021 VALUES LESS THAN ('2022-01-01'),
    PARTITION p2022 VALUES LESS THAN ('2023-01-01'),
    PARTITION p2023 VALUES LESS THAN ('2024-01-01'),
    PARTITION p2024 VALUES LESS THAN ('2025-01-01'),
    PARTITION p2025 VALUES LESS THAN ('2026-01-01'),
    PARTITION p2026 VALUES LESS THAN ('2027-01-01'),
    PARTITION pmax VALUES LESS THAN (MAXVALUE)
);

CREATE TABLE IF NOT EXISTS seat_dash_contract_seat_daily (
    trade_date DATE NOT NULL,
    product_code VARCHAR(64) NOT NULL,
    contract_code VARCHAR(96) NOT NULL,
    symbol VARCHAR(64) NOT NULL,
    contract_rank INT NOT NULL,
    seat_id CHAR(32) NOT NULL,
    seat_name VARCHAR(128) NOT NULL,
    long_position BIGINT NOT NULL,
    short_position BIGINT NOT NULL,
    net_position BIGINT NOT NULL,
    batch_id BIGINT NOT NULL,
    PRIMARY KEY (trade_date, contract_code, seat_id),
    KEY ix_contract_seat_product (product_code, trade_date, seat_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS seat_dash_combination_signal_daily (
    trade_date DATE NOT NULL,
    product_code VARCHAR(64) NOT NULL,
    combination_id VARCHAR(64) NOT NULL,
    direction VARCHAR(16) NOT NULL,
    net_position BIGINT NOT NULL,
    net_change BIGINT NOT NULL,
    action_code VARCHAR(32) NULL,
    action_change BIGINT NULL,
    member_count BIGINT NOT NULL,
    observed_member_count BIGINT NOT NULL,
    rule_version VARCHAR(64) NOT NULL,
    batch_id BIGINT NOT NULL,
    PRIMARY KEY (trade_date, product_code, combination_id, rule_version),
    KEY ix_combination_signal_date (trade_date, combination_id, product_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS seat_dash_seat_signal_daily (
    trade_date DATE NOT NULL,
    product_code VARCHAR(64) NOT NULL,
    seat_id CHAR(32) NOT NULL,
    seat_name VARCHAR(128) NOT NULL,
    signal_code VARCHAR(64) NOT NULL,
    direction VARCHAR(32) NOT NULL,
    severity VARCHAR(16) NOT NULL,
    metric_value DOUBLE NULL,
    threshold_value DOUBLE NULL,
    confidence DOUBLE NULL,
    evidence_json LONGTEXT NOT NULL,
    rule_version VARCHAR(64) NOT NULL,
    batch_id BIGINT NOT NULL,
    PRIMARY KEY (trade_date, product_code, seat_id, signal_code, rule_version),
    KEY ix_signal_severity (trade_date, severity, signal_code),
    KEY ix_signal_seat (seat_id, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
PARTITION BY RANGE COLUMNS (trade_date) (
    PARTITION p2020 VALUES LESS THAN ('2021-01-01'),
    PARTITION p2021 VALUES LESS THAN ('2022-01-01'),
    PARTITION p2022 VALUES LESS THAN ('2023-01-01'),
    PARTITION p2023 VALUES LESS THAN ('2024-01-01'),
    PARTITION p2024 VALUES LESS THAN ('2025-01-01'),
    PARTITION p2025 VALUES LESS THAN ('2026-01-01'),
    PARTITION p2026 VALUES LESS THAN ('2027-01-01'),
    PARTITION pmax VALUES LESS THAN (MAXVALUE)
);

CREATE TABLE IF NOT EXISTS seat_dash_term_signal_daily (
    trade_date DATE NOT NULL,
    product_code VARCHAR(64) NOT NULL,
    near_contract_code VARCHAR(96) NOT NULL,
    far_contract_code VARCHAR(96) NOT NULL,
    seat_id CHAR(32) NOT NULL,
    seat_name VARCHAR(128) NOT NULL,
    near_net_position BIGINT NOT NULL,
    far_net_position BIGINT NOT NULL,
    direction VARCHAR(64) NOT NULL,
    min_leg_position BIGINT NOT NULL,
    leg_count BIGINT NOT NULL,
    legs_json LONGTEXT NOT NULL,
    severity VARCHAR(16) NOT NULL,
    confidence DOUBLE NULL,
    evidence_json LONGTEXT NOT NULL,
    rule_version VARCHAR(64) NOT NULL,
    batch_id BIGINT NOT NULL,
    PRIMARY KEY (trade_date, product_code, seat_id, rule_version),
    KEY ix_term_product (product_code, trade_date, severity)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
PARTITION BY RANGE COLUMNS (trade_date) (
    PARTITION p2020 VALUES LESS THAN ('2021-01-01'),
    PARTITION p2021 VALUES LESS THAN ('2022-01-01'),
    PARTITION p2022 VALUES LESS THAN ('2023-01-01'),
    PARTITION p2023 VALUES LESS THAN ('2024-01-01'),
    PARTITION p2024 VALUES LESS THAN ('2025-01-01'),
    PARTITION p2025 VALUES LESS THAN ('2026-01-01'),
    PARTITION p2026 VALUES LESS THAN ('2027-01-01'),
    PARTITION pmax VALUES LESS THAN (MAXVALUE)
);

CREATE TABLE IF NOT EXISTS seat_dash_rule_result_daily (
    trade_date DATE NOT NULL,
    product_code VARCHAR(64) NOT NULL,
    rule_code VARCHAR(64) NOT NULL,
    horizon_days INT NOT NULL,
    signal_direction VARCHAR(32) NOT NULL,
    current_percentile DOUBLE NULL,
    sample_size BIGINT NOT NULL,
    mean_return DOUBLE NULL,
    median_return DOUBLE NULL,
    win_rate DOUBLE NULL,
    confidence_low DOUBLE NULL,
    confidence_high DOUBLE NULL,
    band VARCHAR(16) NOT NULL,
    signal_label VARCHAR(32) NOT NULL,
    confidence_label VARCHAR(16) NOT NULL,
    library_cutoff DATE NULL,
    status VARCHAR(32) NOT NULL,
    return_basis VARCHAR(64) NOT NULL,
    rule_version VARCHAR(64) NOT NULL,
    batch_id BIGINT NOT NULL,
    PRIMARY KEY (trade_date, product_code, rule_code, horizon_days, rule_version),
    KEY ix_rule_product_date (product_code, trade_date, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
PARTITION BY RANGE COLUMNS (trade_date) (
    PARTITION p2020 VALUES LESS THAN ('2021-01-01'),
    PARTITION p2021 VALUES LESS THAN ('2022-01-01'),
    PARTITION p2022 VALUES LESS THAN ('2023-01-01'),
    PARTITION p2023 VALUES LESS THAN ('2024-01-01'),
    PARTITION p2024 VALUES LESS THAN ('2025-01-01'),
    PARTITION p2025 VALUES LESS THAN ('2026-01-01'),
    PARTITION p2026 VALUES LESS THAN ('2027-01-01'),
    PARTITION pmax VALUES LESS THAN (MAXVALUE)
);

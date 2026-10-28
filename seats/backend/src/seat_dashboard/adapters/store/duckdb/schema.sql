CREATE TABLE IF NOT EXISTS seat_dash_publish_state (
    dataset_name VARCHAR PRIMARY KEY,
    batch_id BIGINT NOT NULL,
    trade_date DATE,
    published_at TIMESTAMP NOT NULL,
    data_backend VARCHAR NOT NULL,
    complete BOOLEAN NOT NULL,
    metadata_json VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS seat_dash_etl_run (
    batch_id BIGINT PRIMARY KEY,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    status VARCHAR NOT NULL,
    mode VARCHAR NOT NULL,
    source_manifest_sha256 VARCHAR,
    date_min DATE,
    date_max DATE,
    row_counts_json VARCHAR NOT NULL,
    validation_json VARCHAR NOT NULL,
    error_message VARCHAR
);

CREATE TABLE IF NOT EXISTS seat_dash_product_dim (
    product_code VARCHAR PRIMARY KEY,
    root VARCHAR NOT NULL,
    exchange VARCHAR NOT NULL,
    display_name VARCHAR NOT NULL,
    active BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS seat_dash_contract_dim (
    contract_code VARCHAR PRIMARY KEY,
    product_code VARCHAR NOT NULL,
    exchange VARCHAR NOT NULL,
    symbol VARCHAR,
    contract_month INTEGER,
    contract_order_source VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS seat_dash_seat_dim (
    seat_id VARCHAR PRIMARY KEY,
    seat_name VARCHAR NOT NULL,
    active BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS seat_dash_seat_alias (
    source_seat_name VARCHAR PRIMARY KEY,
    seat_id VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    note VARCHAR
);

CREATE TABLE IF NOT EXISTS seat_dash_tag_definition (
    tag_code VARCHAR PRIMARY KEY,
    label VARCHAR NOT NULL,
    category VARCHAR NOT NULL,
    color_token VARCHAR NOT NULL,
    description VARCHAR NOT NULL,
    is_dynamic BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS seat_dash_seat_tag_assignment (
    assignment_id VARCHAR PRIMARY KEY,
    seat_id VARCHAR NOT NULL,
    tag_code VARCHAR NOT NULL,
    product_code VARCHAR,
    valid_from DATE,
    valid_to DATE,
    source VARCHAR NOT NULL,
    confidence DOUBLE,
    evidence_json VARCHAR NOT NULL,
    note VARCHAR
);

CREATE TABLE IF NOT EXISTS seat_dash_combination (
    combination_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    scope VARCHAR NOT NULL,
    is_system BOOLEAN NOT NULL,
    active BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS seat_dash_combination_member (
    combination_id VARCHAR NOT NULL,
    seat_id VARCHAR NOT NULL,
    PRIMARY KEY (combination_id, seat_id)
);

CREATE TABLE IF NOT EXISTS seat_dash_market_daily (
    trade_date DATE NOT NULL,
    product_code VARCHAR NOT NULL,
    exchange VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    close DOUBLE,
    open_interest BIGINT,
    volume BIGINT,
    sequence INTEGER,
    batch_id BIGINT NOT NULL,
    PRIMARY KEY (trade_date, symbol, exchange)
);

CREATE TABLE IF NOT EXISTS seat_dash_product_daily (
    trade_date DATE NOT NULL,
    product_code VARCHAR NOT NULL,
    root VARCHAR NOT NULL,
    exchange VARCHAR NOT NULL,
    total_long BIGINT NOT NULL,
    total_short BIGINT NOT NULL,
    net_position BIGINT NOT NULL,
    long_change BIGINT NOT NULL,
    short_change BIGINT NOT NULL,
    net_change BIGINT NOT NULL,
    seat_count BIGINT NOT NULL,
    long_short_ratio DOUBLE,
    long_cr5 DOUBLE,
    short_cr5 DOUBLE,
    net_cr5 DOUBLE,
    divergence_state VARCHAR NOT NULL,
    f2_value DOUBLE,
    f2_status VARCHAR NOT NULL,
    batch_id BIGINT NOT NULL,
    PRIMARY KEY (trade_date, product_code)
);

CREATE TABLE IF NOT EXISTS seat_dash_product_seat_daily (
    trade_date DATE NOT NULL,
    product_code VARCHAR NOT NULL,
    seat_id VARCHAR NOT NULL,
    seat_name VARCHAR NOT NULL,
    long_position BIGINT NOT NULL,
    long_change BIGINT NOT NULL,
    short_position BIGINT NOT NULL,
    short_change BIGINT NOT NULL,
    net_position BIGINT NOT NULL,
    net_change BIGINT NOT NULL,
    abs_net_position BIGINT NOT NULL,
    abs_net_change BIGINT NOT NULL,
    previous_net_position BIGINT,
    net_position_change BIGINT NOT NULL,
    entry_state VARCHAR NOT NULL,
    contract_count BIGINT NOT NULL,
    batch_id BIGINT NOT NULL,
    PRIMARY KEY (trade_date, product_code, seat_id)
);

CREATE TABLE IF NOT EXISTS seat_dash_main_contract_seat_daily (
    trade_date DATE NOT NULL,
    product_code VARCHAR NOT NULL,
    contract_code VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    seat_id VARCHAR NOT NULL,
    seat_name VARCHAR NOT NULL,
    long_position BIGINT NOT NULL,
    long_change BIGINT NOT NULL,
    short_position BIGINT NOT NULL,
    short_change BIGINT NOT NULL,
    net_position BIGINT NOT NULL,
    net_change BIGINT NOT NULL,
    close DOUBLE,
    open_interest BIGINT,
    batch_id BIGINT NOT NULL,
    PRIMARY KEY (trade_date, contract_code, seat_id)
);

CREATE TABLE IF NOT EXISTS seat_dash_contract_daily (
    trade_date DATE NOT NULL,
    product_code VARCHAR NOT NULL,
    contract_code VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    contract_month INTEGER,
    contract_rank INTEGER NOT NULL,
    total_long BIGINT NOT NULL,
    total_short BIGINT NOT NULL,
    net_position BIGINT NOT NULL,
    seat_count BIGINT NOT NULL,
    batch_id BIGINT NOT NULL,
    PRIMARY KEY (trade_date, contract_code)
);

CREATE TABLE IF NOT EXISTS seat_dash_contract_seat_daily (
    trade_date DATE NOT NULL,
    product_code VARCHAR NOT NULL,
    contract_code VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    contract_rank INTEGER NOT NULL,
    seat_id VARCHAR NOT NULL,
    seat_name VARCHAR NOT NULL,
    long_position BIGINT NOT NULL,
    short_position BIGINT NOT NULL,
    net_position BIGINT NOT NULL,
    batch_id BIGINT NOT NULL,
    PRIMARY KEY (trade_date, contract_code, seat_id)
);

CREATE TABLE IF NOT EXISTS seat_dash_combination_signal_daily (
    trade_date DATE NOT NULL,
    product_code VARCHAR NOT NULL,
    combination_id VARCHAR NOT NULL,
    direction VARCHAR NOT NULL,
    net_position BIGINT NOT NULL,
    net_change BIGINT NOT NULL,
    action_code VARCHAR,
    action_change BIGINT,
    member_count BIGINT NOT NULL,
    observed_member_count BIGINT NOT NULL,
    rule_version VARCHAR NOT NULL,
    batch_id BIGINT NOT NULL,
    PRIMARY KEY (trade_date, product_code, combination_id, rule_version)
);

CREATE TABLE IF NOT EXISTS seat_dash_seat_signal_daily (
    trade_date DATE NOT NULL,
    product_code VARCHAR NOT NULL,
    seat_id VARCHAR NOT NULL,
    seat_name VARCHAR NOT NULL,
    signal_code VARCHAR NOT NULL,
    direction VARCHAR NOT NULL,
    severity VARCHAR NOT NULL,
    metric_value DOUBLE,
    threshold_value DOUBLE,
    confidence DOUBLE,
    evidence_json VARCHAR NOT NULL,
    rule_version VARCHAR NOT NULL,
    batch_id BIGINT NOT NULL,
    PRIMARY KEY (trade_date, product_code, seat_id, signal_code, rule_version)
);

CREATE TABLE IF NOT EXISTS seat_dash_term_signal_daily (
    trade_date DATE NOT NULL,
    product_code VARCHAR NOT NULL,
    near_contract_code VARCHAR NOT NULL,
    far_contract_code VARCHAR NOT NULL,
    seat_id VARCHAR NOT NULL,
    seat_name VARCHAR NOT NULL,
    near_net_position BIGINT NOT NULL,
    far_net_position BIGINT NOT NULL,
    direction VARCHAR NOT NULL,
    min_leg_position BIGINT NOT NULL,
    leg_count BIGINT NOT NULL,
    legs_json VARCHAR NOT NULL,
    severity VARCHAR NOT NULL,
    confidence DOUBLE,
    evidence_json VARCHAR NOT NULL,
    rule_version VARCHAR NOT NULL,
    batch_id BIGINT NOT NULL,
    PRIMARY KEY (trade_date, product_code, seat_id, rule_version)
);

CREATE TABLE IF NOT EXISTS seat_dash_rule_result_daily (
    trade_date DATE NOT NULL,
    product_code VARCHAR NOT NULL,
    rule_code VARCHAR NOT NULL,
    horizon_days INTEGER NOT NULL,
    signal_direction VARCHAR NOT NULL,
    current_percentile DOUBLE,
    sample_size BIGINT NOT NULL,
    mean_return DOUBLE,
    median_return DOUBLE,
    win_rate DOUBLE,
    confidence_low DOUBLE,
    confidence_high DOUBLE,
    band VARCHAR NOT NULL,
    signal_label VARCHAR NOT NULL,
    confidence_label VARCHAR NOT NULL,
    library_cutoff DATE,
    status VARCHAR NOT NULL,
    return_basis VARCHAR NOT NULL,
    rule_version VARCHAR NOT NULL,
    batch_id BIGINT NOT NULL,
    PRIMARY KEY (trade_date, product_code, rule_code, horizon_days, rule_version)
);

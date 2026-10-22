CREATE TABLE IF NOT EXISTS sponsorpulse.agent_traces
(
    ts             DateTime64(3, 'UTC') DEFAULT now64(3),
    match_id       String,
    agent          LowCardinality(String),
    step           String,
    input_summary  String,
    output_summary String,
    model          LowCardinality(String),
    tokens_in      UInt32,
    tokens_out     UInt32,
    cost_usd       Float64,
    latency_ms     Float64
)
ENGINE = MergeTree
ORDER BY (match_id, ts, agent)

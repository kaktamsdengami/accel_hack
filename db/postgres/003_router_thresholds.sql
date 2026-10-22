-- Router thresholds per stadium (T14). Defaults come from env (MEMORY_MAX_DISTANCE, MEMORY_MIN_MARGIN,
-- MEMORY_MIN_REFS) when a stadium has no row. learn_thresholds() (agents/router_policy.py) writes source 'learned'.
-- No FK to stadiums: synthetic and not-yet-registered stadiums must still be tunable.
CREATE TABLE IF NOT EXISTS router_thresholds (
    stadium_id          text PRIMARY KEY,
    memory_max_distance double precision NOT NULL CHECK (memory_max_distance >= 0),
    memory_min_margin   double precision NOT NULL CHECK (memory_min_margin >= 0),
    min_refs            integer NOT NULL DEFAULT 3 CHECK (min_refs >= 0),
    source              text NOT NULL DEFAULT 'default' CHECK (source IN ('default', 'learned', 'human')),
    samples             integer NOT NULL DEFAULT 0,
    accuracy            double precision,
    escalation_rate     double precision,
    updated_at          timestamptz NOT NULL DEFAULT now()
)

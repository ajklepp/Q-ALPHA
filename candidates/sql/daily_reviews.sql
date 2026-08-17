-- Q-ALPHA daily session reviews (run once in Supabase SQL editor)
CREATE TABLE IF NOT EXISTS daily_reviews (
    id                    SERIAL PRIMARY KEY,
    review_date           TEXT UNIQUE,
    summary               TEXT,
    entered_count         INT DEFAULT 0,
    skipped_count         INT DEFAULT 0,
    expired_count         INT DEFAULT 0,
    pnl                   FLOAT DEFAULT 0,
    win_rate              FLOAT DEFAULT 0,
    improvement_suggestion TEXT,
    full_markdown         TEXT,
    created_at            TIMESTAMP DEFAULT NOW()
);

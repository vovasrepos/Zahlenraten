CREATE TABLE IF NOT EXISTS games (
    id TEXT PRIMARY KEY,
    player_name TEXT NOT NULL CHECK(length(player_name) BETWEEN 1 AND 50),
    secret_number INTEGER NOT NULL CHECK(secret_number BETWEEN 0 AND 100),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    last_guess INTEGER CHECK(last_guess BETWEEN 0 AND 100),
    won INTEGER NOT NULL DEFAULT 0 CHECK(won IN (0, 1)),
    started_at TEXT NOT NULL,
    ended_at TEXT
);

CREATE INDEX IF NOT EXISTS games_player_results
ON games (player_name, won, ended_at);

CREATE TABLE IF NOT EXISTS guesses (
    game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK(sequence > 0),
    number INTEGER NOT NULL CHECK(number BETWEEN 0 AND 100),
    PRIMARY KEY (game_id, sequence)
);

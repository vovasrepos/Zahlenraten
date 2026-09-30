"""Zahlenraten nach Mockup: Flask, sqlite3, HTML/CSS. Kein ORM."""
import os
import random
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Flask, abort, current_app, flash, g, redirect, render_template,
    request, session, url_for,
)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def describe_guess(number, secret_number):
    if number < secret_number:
        return {"number": number, "kind": "low", "label": "zu klein", "arrow": "↑"}
    if number > secret_number:
        return {"number": number, "kind": "high", "label": "zu groß", "arrow": "↓"}
    return {"number": number, "kind": "correct", "label": "richtig", "arrow": "✓"}


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        DATABASE=str(Path(app.instance_path) / "zahlenraten.db"),
        SECRET_KEY=os.environ.get("SECRET_KEY"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=16 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    if not app.config["SECRET_KEY"]:
        key_file = Path(app.instance_path) / "secret_key.txt"
        try:
            with key_file.open("x", encoding="utf-8") as file:
                file.write(secrets.token_hex(32))
        except FileExistsError:
            pass
        app.config["SECRET_KEY"] = key_file.read_text(encoding="utf-8").strip()

    app.teardown_appcontext(close_db)
    with app.app_context():
        schema = (Path(app.root_path) / "schema.sql").read_text(encoding="utf-8")
        db = get_db()
        db.executescript(schema)
        # Alte Datenbanken behalten ihre Ergebnisse. Nur der letzte alte Tipp
        # ist bekannt und kann in die neue Verlaufstabelle übernommen werden.
        db.execute("""INSERT INTO guesses (game_id, sequence, number)
            SELECT id, attempts, last_guess FROM games
            WHERE attempts > 0 AND last_guess IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM guesses WHERE game_id = games.id)""")
        db.commit()

    @app.before_request
    def protect_forms():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(32)
        if request.method == "POST":
            token = request.form.get("csrf_token", "")
            if not secrets.compare_digest(token, session["csrf_token"]):
                abort(400, description="Formular abgelaufen. Bitte lade die Seite neu.")

    def current_game():
        return get_db().execute(
            "SELECT * FROM games WHERE id = ?", (session.get("game_id"),)
        ).fetchone()

    def game_history(game_row):
        rows = get_db().execute(
            "SELECT sequence, number FROM guesses WHERE game_id = ? ORDER BY sequence",
            (game_row["id"],),
        ).fetchall()
        return [dict(describe_guess(row["number"], game_row["secret_number"]),
                     sequence=row["sequence"]) for row in rows]

    def leaderboard(game_id=None):
        # Gleich viele Versuche = gleicher Platz (z. B. 1, 1, 3).
        # Pro abgeschlossener Runde ein Eintrag, nicht pro Benutzerkonto.
        ranked = """WITH ranked AS (
            SELECT id, player_name, attempts, ended_at,
                   RANK() OVER (ORDER BY attempts) AS place
            FROM games WHERE won = 1
        ) """
        db = get_db()
        leaders = db.execute(ranked + """SELECT * FROM ranked
            ORDER BY attempts, ended_at, id LIMIT 10""").fetchall()
        own = db.execute(ranked + "SELECT * FROM ranked WHERE id = ?", (game_id,)).fetchone()
        if own and not any(row["id"] == game_id for row in leaders):
            leaders.append(own)
        return leaders, own

    @app.route("/", methods=["GET", "POST"])
    def index():
        name, error = "", None
        if request.method == "POST":
            name = request.form.get("player_name", "").strip()
            if not 1 <= len(name) <= 50:
                error = "Bitte gib einen Namen mit 1 bis 50 Zeichen ein."
            else:
                game_id = secrets.token_hex(16)
                db = get_db()
                db.execute(
                    """INSERT INTO games (id, player_name, secret_number, started_at)
                       VALUES (?, ?, ?, ?)""",
                    (game_id, name, random.randint(0, 100), now()),
                )
                db.commit()
                session["game_id"] = game_id
                session.pop("_flashes", None)
                session.pop("invalid_guess", None)
                return redirect(url_for("game"))
        leaders, _ = leaderboard()
        return render_template("index.html", name=name, error=error, leaders=leaders)

    @app.route("/game", methods=["GET", "POST"])
    def game():
        game_row = current_game()
        if game_row is None:
            return redirect(url_for("index"))
        if game_row["won"]:
            return redirect(url_for("result"))

        if request.method == "POST":
            raw = request.form.get("guess", "").strip()
            error = None
            if len(raw) > 16 or not re.fullmatch(r"[+-]?[0-9]+", raw):
                error = "Bitte gib eine ganze Zahl von 0 bis 100 ein. Dieser Tipp wird nicht gezählt."
            elif not 0 <= int(raw) <= 100:
                error = f"{raw} liegt außerhalb des Spielbereichs. Bitte gib eine ganze Zahl von 0 bis 100 ein. Dieser Tipp wird nicht gezählt."
            if error:
                flash(error, "error")
                session["invalid_guess"] = raw[:100]
                return redirect(url_for("game"))

            guess = int(raw)
            try:
                expected_attempts = int(request.form.get("attempts", ""))
            except ValueError:
                abort(400, description="Ungültiges Formular. Bitte lade die Seite neu.")
            won = int(guess == game_row["secret_number"])
            db = get_db()
            # Zähler und Verlauf werden gemeinsam gespeichert (Transaktion).
            with db:
                updated = db.execute(
                    """UPDATE games
                       SET attempts = attempts + 1, last_guess = ?, won = ?, ended_at = ?
                       WHERE id = ? AND attempts = ? AND won = 0""",
                    (guess, won, now() if won else None, game_row["id"], expected_attempts),
                )
                if updated.rowcount:
                    db.execute("INSERT INTO guesses (game_id, sequence, number) VALUES (?, ?, ?)",
                               (game_row["id"], expected_attempts + 1, guess))
                else:
                    flash("Diese Ansicht ist veraltet. Bitte verwende den aktuellen Stand.", "error")
            return redirect(url_for("game"))

        history = game_history(game_row)
        # Grenzen beruhen nur auf bisherigen Tipps, nicht auf dem Geheimnis allein.
        lower = max([0] + [tip["number"] + 1 for tip in history if tip["kind"] == "low"])
        upper = min([100] + [tip["number"] - 1 for tip in history if tip["kind"] == "high"])
        return render_template(
            "game.html", game=game_row, active_game=True, history=history,
            feedback=history[-1] if history else None, lower=lower, upper=upper,
            raw_guess=session.pop("invalid_guess", ""),
        )

    @app.get("/result")
    def result():
        game_row = current_game()
        if game_row is None:
            return redirect(url_for("index"))
        if not game_row["won"]:
            return redirect(url_for("game"))
        leaders, own = leaderboard(game_row["id"])
        return render_template("result.html", game=game_row, history=game_history(game_row),
                               leaders=leaders, own=own)

    @app.post("/end")
    def end_game():
        # Abgebrochene Runden zählen nicht für die Bestenliste.
        session.pop("game_id", None)
        session.pop("invalid_guess", None)
        session.pop("_flashes", None)
        return redirect(url_for("index"))

    @app.get("/rules")
    def rules():
        return render_template("rules.html")

    @app.errorhandler(400)
    def bad_request(error):
        return render_template("error.html", message=error.description), 400

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000)




"""Funktionsprüfungen mit einer temporären, getrennten SQLite-Datenbank."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app


class GameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp.name) / "test.db")
        self.config = {"TESTING": True, "SECRET_KEY": "only-for-tests", "DATABASE": self.database}
        self.app = create_app(self.config)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def post(self, url, data, client=None):
        client = client or self.client
        client.get("/")
        with client.session_transaction() as session:
            token = session["csrf_token"]
        return client.post(url, data={**data, "csrf_token": token}, follow_redirects=True)

    def start(self, number=44, name="Volodymyr", client=None):
        with patch("app.random.randint", return_value=number):
            return self.post("/", {"player_name": name}, client)

    def row(self, client=None):
        with (client or self.client).session_transaction() as session:
            game_id = session["game_id"]
        with sqlite3.connect(self.database) as db:
            db.row_factory = sqlite3.Row
            return db.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()

    def guess(self, number):
        return self.post("/game", {"guess": str(number), "attempts": self.row()["attempts"]})

    def test_complete_game_and_history(self):
        self.start()
        self.assertIn("63 ist zu groß.", self.guess(63).get_data(as_text=True))
        self.assertIn("30 ist zu klein.", self.guess(30).get_data(as_text=True))
        self.guess(42)
        page = self.guess(44).get_data(as_text=True)
        self.assertIn("Richtig! Die Zahl war 44.", page)
        self.assertIn("4 Versuche", page)
        self.assertEqual(self.row()["attempts"], 4)
        self.assertEqual(self.row()["won"], 1)
        self.assertIsNotNone(self.row()["ended_at"])
        # Eine neue Runde zeigt die alte abgeschlossene Runde.
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("<td>4</td>", page)
        self.start()
        self.assertEqual(self.row()["attempts"], 0)

    def test_invalid_input_does_not_count(self):
        self.start()
        for value in ["", "abc", "3.5", "-1", "101", "1_0", "9" * 100]:
            with self.subTest(value=value):
                self.assertEqual(self.guess(value).status_code, 200)
                self.assertEqual(self.row()["attempts"], 0)

    def test_refresh_and_duplicate_submit_do_not_count_twice(self):
        self.start()
        self.guess(20)
        self.client.get("/game")
        self.post("/game", {"guess": "20", "attempts": "0"})
        self.assertEqual(self.row()["attempts"], 1)
        self.guess(44)
        self.client.get("/result")
        self.post("/game", {"guess": "44", "attempts": "2"})
        self.assertEqual(self.row()["attempts"], 2)

    def test_boundaries_and_separate_browsers(self):
        self.start(number=0)
        second = self.app.test_client()
        self.start(number=100, name="Anna", client=second)
        self.guess(0)
        self.assertEqual(self.row()["won"], 1)
        self.assertEqual(self.row(second)["attempts"], 0)
        self.post("/game", {"guess": "100", "attempts": "0"}, client=second)
        self.assertEqual(self.row(second)["won"], 1)

    def test_database_survives_app_restart(self):
        self.start()
        self.guess(44)
        restarted = create_app(self.config).test_client()
        page = restarted.get("/").get_data(as_text=True)
        self.assertIn("<td>1</td>", page)

    def test_name_validation_sql_and_html_characters(self):
        for name in ["   ", "X" * 51]:
            page = self.post("/", {"player_name": name}).get_data(as_text=True)
            self.assertIn("Bitte gib einen Namen", page)
        name = "<script>alert(1)</script>'"
        page = self.start(name=name).get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.guess(44)
        self.assertEqual(self.row()["player_name"], name)

    def test_missing_session_and_form_token(self):
        self.assertEqual(self.client.get("/game").status_code, 302)
        self.assertEqual(self.client.get("/result").status_code, 302)
        self.assertEqual(self.client.post("/", data={"player_name": "Test"}).status_code, 400)
        self.start()
        with self.client.session_transaction() as session:
            self.assertNotIn("secret_number", session)
        self.assertEqual(self.client.get("/result").location, "/game")

    def test_mockup_history_range_and_invalid_input(self):
        self.start(number=47, name="Lena")
        for number in [50, 25, 42]:
            page = self.guess(number).get_data(as_text=True)
        self.assertIn("Noch möglich: 43 bis 49", page)
        self.assertIn("42 ist zu klein.", page)
        page = self.guess(150).get_data(as_text=True)
        self.assertIn("150 liegt außerhalb des Spielbereichs", page)
        self.assertIn('value="150"', page)
        self.assertEqual(self.row()["attempts"], 3)
        with sqlite3.connect(self.database) as db:
            values = db.execute("SELECT number FROM guesses ORDER BY sequence").fetchall()
        self.assertEqual(values, [(50,), (25,), (42,)])
        self.guess(46)
        page = self.guess(47).get_data(as_text=True)
        self.assertIn("5 Versuche", page)
        self.assertIn("Dein Weg zur Zahl", page)
        self.assertIn('class="own-result"', page)

    def test_leaderboard_ranking_ties_and_abandon(self):
        self.start(name="Two attempts")
        self.guess(50)
        self.guess(44)
        for name in ["First", "Tied"]:
            self.start(name=name)
            page = self.guess(44).get_data(as_text=True)
            self.assertIn("Platz 1", page)
        self.start(name="Abandoned")
        self.guess(50)
        page = self.post("/end", {}).get_data(as_text=True)
        self.assertNotIn("Abandoned", page)
        self.assertIn("<td>3</td>", page)
        self.assertLess(page.index('class="leader-name">First'), page.index('class="leader-name">Two attempts'))
        self.assertEqual(self.client.get("/game").location, "/")

    def test_previous_database_migrates_without_losing_results(self):
        self.start()
        self.guess(20)
        self.guess(44)
        # Simuliert das alte Schema ohne Verlaufstabelle.
        with sqlite3.connect(self.database) as db:
            db.execute("DROP TABLE guesses")
        create_app(self.config)
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT attempts, won FROM games").fetchone(), (2, 1))
            self.assertEqual(db.execute("SELECT sequence, number FROM guesses").fetchall(), [(2, 44)])

    def test_own_result_visible_beyond_top_ten(self):
        for i in range(11):
            self.start(name=f"Winner {i}")
            self.guess(44)
        self.start(name="Lena")
        self.guess(50)
        page = self.guess(44).get_data(as_text=True)
        self.assertIn("Platz 12", page)
        self.assertIn('class="own-result"', page)
        self.assertIn('Lena <span class="you-badge">Du</span>', page)


if __name__ == "__main__":
    unittest.main()

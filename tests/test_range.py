"""The date range: presets and custom dates, as on the US dashboard, and
remembered across pages - a range picked on Affiliates holds on Lenders
and the rest until another is picked."""

import datetime as dt
import re
import sys
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent / "service"
if str(SERVICE) not in sys.path:
    sys.path.insert(0, str(SERVICE))

import main                                                # noqa: E402
import store                                               # noqa: E402
from tests.test_logins import Server                       # noqa: E402

STAMP = "2026-10-15T00:00:00+00:00"


class Resolve(unittest.TestCase):
    def test_the_default_is_thirty_days(self):
        rng, picked = main.resolve_range({}, None)
        self.assertEqual((rng["days"], rng["start"], picked), (30, None, False))

    def test_a_preset_from_the_query_is_picked(self):
        rng, picked = main.resolve_range({"days": "14"}, "90")
        self.assertEqual((rng["days"], picked), (14, True))

    def test_the_cookie_is_used_when_the_query_names_none(self):
        rng, picked = main.resolve_range({}, "56")
        self.assertEqual((rng["days"], picked), (56, False))

    def test_a_custom_range_ends_no_later_than_yesterday(self):
        today = store.business_today()
        rng, _ = main.resolve_range({"from": (today - dt.timedelta(days=5)).isoformat(),
                                     "to": (today + dt.timedelta(days=3)).isoformat()}, None)
        self.assertEqual(rng["end"], (today - dt.timedelta(days=1)).isoformat())
        self.assertEqual(rng["days"], 5)

    def test_dates_given_backwards_are_swapped(self):
        rng, _ = main.resolve_range({"from": "2026-10-10", "to": "2026-10-01"}, None)
        self.assertEqual((rng["start"], rng["end"]), ("2026-10-01", "2026-10-10"))

    def test_rubbish_falls_back_to_the_default(self):
        rng, _ = main.resolve_range({"from": "x", "to": "y", "days": "z"}, "nonsense")
        self.assertEqual(rng["days"], 30)


class Window(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.addCleanup(store.set_window)
        for day, n in (("2026-10-01", 1), ("2026-10-05", 10), ("2026-10-09", 100)):
            store.save_lender_results(self.conn, day, {(5, 50, 1): (n, n, 0.0)},
                                      {"pings": n, "leads": n, "applications": n}, STAMP)

    def test_a_custom_range_bounds_both_ends(self):
        store.set_window("2026-10-02", "2026-10-06", "2 Oct – 6 Oct", "2 Oct – 6 Oct", 5)
        self.assertEqual(store.lender_result_reach(self.conn, 30)[2], 10)

    def test_without_one_the_window_is_rolling(self):
        store.set_window()
        self.assertEqual(store.window_end(), store.OPEN_END)


class Remembered(Server):
    def selected(self, browser, path):
        text = browser.get(path)[2]
        m = re.search(r'<option value="([^"]+)" selected>', text)
        return m.group(1) if m else None

    def test_a_range_picked_on_one_page_holds_on_the_others(self):
        b = self.admin()
        b.get("/?days=14")
        for path in ("/lenders", "/customers", "/scorecard"):
            with self.subTest(path=path):
                self.assertEqual(self.selected(b, path), "14")

    def test_a_custom_range_is_remembered_too(self):
        b = self.admin()
        b.get("/lenders?from=2026-09-01&to=2026-09-30")
        self.assertEqual(self.selected(b, "/"), "custom")
        self.assertIn('value="2026-09-01"', b.get("/customers")[2])

    def test_today_and_yesterday_have_no_range(self):
        b = self.admin()
        self.assertNotIn('id="rangeSel"', b.get("/yesterday")[2])


if __name__ == "__main__":
    unittest.main()

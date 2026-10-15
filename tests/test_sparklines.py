"""Every sparkline opens into a pop-up with the full chart.

Layton's rule, 15 Oct 2026, for every sparkline now and added later: a
sparkline shows direction but not size, and whoever sees a red dot needs
the numbers behind it. `sparkline()` refuses to draw without an expand
description; this holds every call site to passing one, on every page.
"""

import re
import unittest
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent / "service"
PAGES = sorted(SERVICE.glob("*.html"))


def calls(source, name):
    """The argument lists of every call to `name`, definitions excluded."""
    out = []
    for m in re.finditer(r"(?<![\w.])" + name + r"\(", source):
        if source[max(0, m.start() - 9):m.start()].endswith("function "):
            continue
        depth, i, args, start = 1, m.end(), [], m.end()
        while depth:
            c = source[i]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            elif c == "," and depth == 1:
                args.append(source[start:i]); start = i + 1
            i += 1
        args.append(source[start:i - 1])
        out.append([a.strip() for a in args if a.strip()])
    return out


class EverySparklineExpands(unittest.TestCase):
    def test_the_pages_draw_sparklines(self):
        self.assertTrue(any(calls(p.read_text(), "sparkline") for p in PAGES))

    def test_every_call_passes_an_expand_description(self):
        for page in PAGES:
            for args in calls(page.read_text(), "sparkline"):
                with self.subTest(page=page.name, call=args):
                    self.assertGreaterEqual(len(args), 3, "sparkline without a pop-up")
                    self.assertIn("title", args[2])

    def test_a_page_with_sparklines_has_the_pop_up(self):
        for page in PAGES:
            source = page.read_text()
            if calls(source, "sparkline"):
                with self.subTest(page=page.name):
                    self.assertIn('id="chartPop"', source)
                    self.assertIn("function openChart", source)


if __name__ == "__main__":
    unittest.main()

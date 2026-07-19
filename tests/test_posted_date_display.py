import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AddedTimeDisplayTests(unittest.TestCase):
    def test_public_list_uses_added_time(self):
        template = (ROOT / "app/templates/index.html").read_text(encoding="utf-8")

        self.assertIn("const dateVal    = seenDate;", template)
        self.assertIn("const dateLabel  = hasDate ? 'Added' : '';", template)
        self.assertIn("Jobs by added time", template)

    def test_signed_in_list_uses_added_time(self):
        template = (ROOT / "app/templates/user.html").read_text(encoding="utf-8")

        self.assertIn("const dateVal    = seenDate || '—';", template)
        self.assertIn('<th style="width:96px">Added</th>', template)
        self.assertIn("Jobs by added time", template)


if __name__ == "__main__":
    unittest.main()

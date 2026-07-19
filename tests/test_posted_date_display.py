import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PostedDateDisplayTests(unittest.TestCase):
    def test_public_list_prefers_posted_and_falls_back_to_first_seen(self):
        template = (ROOT / "app/templates/index.html").read_text(encoding="utf-8")

        self.assertIn("const postedDate = (j.posted || '').slice(0, 10);", template)
        self.assertIn("postedDate.length >= 8 ? postedDate : seenDate", template)
        self.assertIn("const dateLabel  = hasDate ? 'Posted' : '';", template)

    def test_signed_in_list_prefers_posted_and_labels_column_posted(self):
        template = (ROOT / "app/templates/user.html").read_text(encoding="utf-8")

        self.assertIn("const postedDate = (j.posted || '').slice(0, 10);", template)
        self.assertIn("postedDate.length >= 8 ? postedDate : (seenDate || '—')", template)
        self.assertIn('<th style="width:96px">Posted</th>', template)


if __name__ == "__main__":
    unittest.main()

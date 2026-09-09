import unittest
from unittest.mock import Mock, patch

from app.api.ats.careyaya import fetch_careyaya


class CareYayaTests(unittest.TestCase):
    @patch("app.api.ats.careyaya.requests.get")
    def test_uses_card_title_instead_of_interest_link_text(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.text = """
        <div class="py-4 flex flex-col">
          <p class="font-bold text-xl">Dementia Care Navigator</p>
          <p class="text-gray-500">Fully Remote</p>
          <p class="text-gray-500">Flexible Start</p>
          <div><a href="/careers/dementia-care-navigator/random-uuid">I'm Interested</a></div>
        </div>
        """
        get.return_value = response

        jobs, error = fetch_careyaya(
            {"list_url": "https://www.careyaya.org/careers"}, {}, "CareYaya"
        )

        self.assertIsNone(error)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Dementia Care Navigator")
        self.assertEqual(jobs[0]["location"], "Fully Remote")
        self.assertEqual(jobs[0]["ats_id"], "dementia-care-navigator")
        self.assertEqual(jobs[0]["source"], "careyaya")


if __name__ == "__main__":
    unittest.main()

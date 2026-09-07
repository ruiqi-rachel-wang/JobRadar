import unittest
from unittest.mock import Mock, patch

from app.api.ats.kula import fetch_kula


class KulaTests(unittest.TestCase):
    @patch("app.api.ats.kula.requests.get")
    def test_parses_metadata_from_apply_link_card(self, mock_get):
        response = Mock()
        response.text = """
        <div class="chakra-card css-generated">
          <span class="chakra-stack">
            <span><span>Software</span></span>
            <div><p class="chakra-text">Full-Stack Software Engineer</p></div>
            <div>
              <p class="chakra-text">Santa Clara, California, United States; Addison, Texas, United States + 2 locations</p>
              <p class="chakra-text">USD 168,000 - 210,000 / year</p>
              <p class="chakra-text">Full Time • Hybrid</p>
            </div>
            <a href="/precision-neuroscience/25805/"><span>Apply Now</span></a>
          </span>
        </div>
        """
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        jobs, error = fetch_kula(
            {
                "list_url": "https://careers.kula.ai/precision-neuroscience",
                "company_slug": "precision-neuroscience",
            },
            set(),
            "Precision Neuroscience",
        )

        self.assertIsNone(error)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Full-Stack Software Engineer")
        self.assertEqual(jobs[0]["ats_id"], "25805")
        self.assertEqual(jobs[0]["department"], "Software")
        self.assertEqual(jobs[0]["employment_type"], "Full Time • Hybrid")
        self.assertEqual(
            jobs[0]["locations_raw"],
            [
                "Santa Clara, California, United States",
                "Addison, Texas, United States",
            ],
        )


if __name__ == "__main__":
    unittest.main()

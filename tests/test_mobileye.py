import json
import unittest
from unittest.mock import Mock, patch

from app.api.ats.mobileye import fetch_mobileye


class MobileyeTests(unittest.TestCase):
    @patch("app.api.ats.mobileye.requests.get")
    def test_parses_nuxt_reference_payload(self, mock_get):
        payload = [None] * 18
        payload[0] = [1]
        payload[1] = {"jobid": 2, "text": 3, "team": 4}
        payload[2] = "abc-123"
        payload[3] = "Software Engineer, Platform"
        payload[4] = "Software"
        payload[5] = [6]
        payload[6] = {
            "jobid": 2,
            "text": 3,
            "location": 7,
            "department": 8,
            "commitment": 9,
            "descriptionPlain": 10,
            "lists": 11,
        }
        payload[7] = "New York, US"
        payload[8] = "R&D"
        payload[9] = "Full time"
        payload[10] = "Build autonomous-driving systems."
        payload[11] = [12]
        payload[12] = {"text": 13, "content": 14}
        payload[13] = "What you'll do"
        payload[14] = "<li>Ship reliable software.</li>"

        response = Mock()
        response.text = (
            '<html><script id="__NUXT_DATA__" type="application/json">'
            + json.dumps(payload)
            + "</script></html>"
        )
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        jobs, error = fetch_mobileye({}, {}, "Mobileye")

        self.assertIsNone(error)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Software Engineer, Platform")
        self.assertEqual(jobs[0]["location"], "New York, US")
        self.assertEqual(jobs[0]["department"], "R&D")
        self.assertEqual(jobs[0]["employment_type"], "Full time")
        self.assertEqual(
            jobs[0]["url"],
            "https://careers.mobileye.com/jobs/software-engineer-platform/abc-123",
        )
        self.assertIn("Ship reliable software", jobs[0]["description"])


if __name__ == "__main__":
    unittest.main()

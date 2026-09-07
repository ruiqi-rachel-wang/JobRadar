import unittest
from unittest.mock import patch

from app.api.apis import _enrich
from app.api.ats.jobvite import fetch_jobvite


class _Response:
    def __init__(self, text, url="https://jobs.jobvite.com/nutanix/jobs"):
        self.text = text
        self.url = url

    def raise_for_status(self):
        return None


class JobviteExperienceTests(unittest.TestCase):
    @patch("app.api.ats.jobvite.requests.get")
    def test_detail_description_populates_numeric_experience(self, get):
        listing = _Response(
            "<div><a href='/nutanix/job/oQyAAfwi'>"
            "Systems Reliability Engineer II</a><span>Pune, India</span></div>"
        )
        detail = _Response(
            "<section class='jv-job-detail-description'>"
            "<h2>Qualifications</h2>"
            "<p>Minimum 2 years relative working experience.</p>"
            "<p>Work with customers and engineering teams to operate reliable "
            "cloud infrastructure and resolve complex production issues.</p>"
            "</section>",
            "https://jobs.jobvite.com/nutanix/job/oQyAAfwi",
        )
        get.side_effect = lambda url, **_kwargs: (
            detail if "/job/oQyAAfwi" in url else listing
        )

        jobs, error = fetch_jobvite(
            {
                "list_url": "https://jobs.jobvite.com/nutanix/jobs",
                "fetch_experience_details": True,
                "max_experience_details": 10,
            },
            {},
            "Nutanix",
        )
        _enrich(jobs, {}, "Nutanix", scan_type="fresh")

        self.assertIsNone(error)
        self.assertEqual("2+ years", jobs[0]["exp_label"])
        self.assertEqual(2, get.call_count)


if __name__ == "__main__":
    unittest.main()

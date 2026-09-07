import unittest
from unittest.mock import patch

from app.api.ats.cornerstone import fetch_cornerstone


class _Response:
    def __init__(self, *, text="", payload=None):
        self.text = text
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class CornerstoneTests(unittest.TestCase):
    @patch("app.api.ats.cornerstone.requests.post")
    @patch("app.api.ats.cornerstone.requests.get")
    def test_fetches_all_pages_and_preserves_raw_fields(self, get, post):
        get.return_value = _Response(
            text=(
                'csod.context.token = "signed-token";'
                'window.api = "https://us.api.csod.com";'
            )
        )

        def page_response(_url, **kwargs):
            page = kwargs["json"]["pageNumber"]
            req = {
                "requisitionId": str(page),
                "displayJobTitle": f"Engineer {page}",
                "locations": [{
                    "city": "Champaign",
                    "state": "Illinois",
                    "country": "United States",
                }],
                "schedule": "Full-time",
                "department": "Engineering",
                "postingEffectiveDate": "7/29/2026",
                "externalDescription": "<h2>Role</h2><ul><li>Build systems</li></ul>",
            }
            return _Response(payload={
                "data": {"totalCount": 2, "requisitions": [req]},
            })

        post.side_effect = page_response
        jobs, error = fetch_cornerstone(
            {
                "list_url": "https://illinois.csod.com/ux/ats/careersite/1/home?c=illinois",
                "site_id": 1,
                "page_size": 1,
            },
            {},
            "University of Illinois Urbana-Champaign",
        )

        self.assertIsNone(error)
        self.assertEqual(2, len(jobs))
        self.assertEqual({"1", "2"}, {job["ats_id"] for job in jobs})
        self.assertEqual("Champaign, IL, US", jobs[0]["location"])
        self.assertEqual(
            "Champaign, Illinois, United States",
            jobs[0]["location_raw"],
        )
        self.assertEqual("Full-time", jobs[0]["employment_type_raw"])
        self.assertIn("<h2>Role</h2>", jobs[0]["description_raw"])
        self.assertEqual("cornerstone", jobs[0]["source"])

    @patch("app.api.ats.cornerstone.requests.get")
    def test_missing_token_is_an_explicit_error(self, get):
        get.return_value = _Response(text="<html>No token</html>")
        jobs, error = fetch_cornerstone(
            {"cornerstone_slug": "example"},
            {},
            "Example",
        )
        self.assertEqual([], jobs)
        self.assertIn("token not found", error)


if __name__ == "__main__":
    unittest.main()

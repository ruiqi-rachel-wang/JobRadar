import unittest
from unittest.mock import patch

from app.api.ats.paradox import fetch_paradox


class _Response:
    def __init__(self, payload=None):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _job(number):
    return {
        "uniqueID": f"stable-{number}",
        "requisitionID": f"REQ-{number}",
        "reference": f"REQ-{number}",
        "title": f"Engineer {number}",
        "description": "<h2>Role</h2><ul><li>Build systems</li></ul>",
        "locations": [{
            "locationText": "123 Main St, New York, New York, 10001, United States",
            "city": "New York",
            "state": "New York",
            "country": "United States",
        }],
        "isRemote": False,
        "employmentType": ["Full Time"],
        "applyURL": f"https://apply.example/{number}",
        "originalURL": f"engineer-{number}/job/stable-{number}",
        "brandName": "Example",
    }


class ParadoxTests(unittest.TestCase):
    @patch("app.api.ats.paradox.requests.post")
    @patch("app.api.ats.paradox.requests.get")
    def test_fetches_all_pages_and_preserves_structured_fields(self, get, post):
        get.return_value = _Response()

        def page_response(_url, **kwargs):
            page = kwargs["params"]["page_number"]
            return _Response({
                "totalJob": 26,
                "jobs": (
                    [_job(number) for number in range(1, 26)]
                    if page == 1 else [_job(26)]
                ),
            })

        post.side_effect = page_response
        jobs, error = fetch_paradox(
            {
                "list_url": "https://careers.example/jobs",
                "page_size": 25,
                "page_workers": 2,
            },
            {},
            "Example",
        )

        self.assertIsNone(error)
        self.assertEqual(26, len(jobs))
        self.assertEqual(
            {"stable-1", "stable-26"},
            {jobs[0]["ats_id"], jobs[-1]["ats_id"]},
        )
        first = jobs[0]
        self.assertEqual("REQ-1", first["requisition_id"])
        self.assertEqual("https://apply.example/1", first["apply_url"])
        self.assertEqual(
            "123 Main St, New York, New York, 10001, United States",
            first["location_raw"],
        )
        self.assertEqual(["Full Time"], first["employment_type_raw"])
        self.assertEqual("onsite", first["workplace_type"])
        self.assertIn("<h2>Role</h2>", first["description_raw"])


if __name__ == "__main__":
    unittest.main()

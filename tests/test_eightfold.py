import unittest
from types import SimpleNamespace
from unittest.mock import patch

import requests as requests_module

from app.api.ats.eightfold import fetch_eightfold


class _Response:
    def __init__(self, payload=None, text=""):
        self.payload = payload
        self.text = text

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _ForbiddenResponse(_Response):
    def raise_for_status(self):
        raise requests_module.HTTPError(
            "forbidden", response=SimpleNamespace(status_code=403)
        )


class EightfoldTests(unittest.TestCase):
    @patch("app.api.ats.eightfold.requests.get")
    def test_uses_returned_page_size_when_api_ignores_num(self, get):
        def payload(job_id, title):
            return {
                "count": 20,
                "positions": [{
                    "id": job_id,
                    "name": title,
                    "canonicalPositionUrl": f"https://example.eightfold.ai/careers/job/{job_id}",
                    "location": "New York, New York, United States of America",
                    "job_description": "Build systems.",
                }],
            }

        get.side_effect = [
            _Response(payload("1", "First")),
            _Response(payload("2", "Second")),
            _Response({"count": 20, "positions": []}),
        ]
        jobs, error = fetch_eightfold(
            {
                "list_url": "https://example.eightfold.ai/careers",
                "eightfold_domain": "example.com",
                "limit": 100,
            },
            {},
            "Example",
        )

        self.assertIsNone(error)
        self.assertEqual([job["ats_id"] for job in jobs], ["1", "2"])
        self.assertEqual(
            [call.kwargs["params"]["start"] for call in get.call_args_list],
            [0, 1, 2],
        )
        self.assertEqual(jobs[0]["description_raw"], "Build systems.")

    @patch("app.api.ats.eightfold.requests.get")
    def test_falls_back_to_public_pcsx_search(self, get):
        pcsx_job = {
            "id": 446718123340,
            "displayJobId": "3080000",
            "name": "Staff Software Engineer",
            "locations": ["San Diego, California, United States of America"],
            "standardizedLocations": ["San Diego, CA, US"],
            "postedTs": 1785801600,
            "department": "Software Engineering",
            "workLocationOption": "hybrid",
            "positionUrl": "/careers/job/446718123340",
        }
        get.side_effect = [
            _ForbiddenResponse(),
            _Response(text='<meta name="_csrf" content="token-123">'),
            _Response({"data": {"count": 1, "positions": [pcsx_job]}}),
        ]

        jobs, error = fetch_eightfold(
            {
                "list_url": "https://careers.qualcomm.com/careers?hl=en-US",
                "eightfold_domain": "qualcomm.com",
            },
            {},
            "Qualcomm",
        )

        self.assertIsNone(error)
        self.assertEqual(1, len(jobs))
        self.assertEqual("Staff Software Engineer", jobs[0]["title"])
        self.assertEqual("San Diego, CA, US", jobs[0]["location"])
        self.assertEqual("3080000", jobs[0]["requisition_id"])
        self.assertEqual(
            "https://careers.qualcomm.com/careers/job/446718123340",
            jobs[0]["url"],
        )
        self.assertEqual(
            "token-123", get.call_args_list[2].kwargs["headers"]["X-CSRF-Token"]
        )

    @patch("app.api.ats.eightfold.requests.get")
    def test_pcsx_only_supports_targeted_query(self, get):
        get.side_effect = [
            _Response(text='<meta name="_csrf" content="token-123">'),
            _Response({"data": {"count": 0, "positions": []}}),
        ]

        jobs, error = fetch_eightfold(
            {
                "list_url": "https://apply.starbucks.com/careers",
                "eightfold_domain": "starbucks.com",
                "pcsx_only": True,
                "query": "software",
                "location": "US",
            },
            {},
            "Starbucks",
        )

        self.assertEqual([], jobs)
        self.assertIsNone(error)
        self.assertEqual(
            "software", get.call_args_list[1].kwargs["params"]["query"]
        )
        self.assertEqual("US", get.call_args_list[1].kwargs["params"]["location"])


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

from app.api.ats.jobsapi import fetch_jobsapi


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def jobs(start, count):
    return [
        {"data": {"req_id": str(index), "title": f"Role {index}", "city": "Seattle", "state": "WA"}}
        for index in range(start, start + count)
    ]


class JobsApiPaginationTests(unittest.TestCase):
    def test_declared_page_parameter_fetches_distinct_pages(self):
        calls = []

        def fake_get(url, params, headers, timeout):
            calls.append(dict(params))
            page = params["page"]
            return FakeResponse({"jobs": jobs(1, 100) if page == 1 else jobs(101, 1), "totalCount": 101})

        scrape = {
            "api_url": "https://example.com/api/jobs",
            "job_url_prefix": "https://example.com/jobs/",
            "page_param": "page",
        }
        with patch("app.api.ats.jobsapi.requests.get", side_effect=fake_get):
            result, error = fetch_jobsapi(scrape, {}, "Example")
        self.assertIsNone(error)
        self.assertEqual(len(result), 101)
        self.assertEqual([call["page"] for call in calls], [1, 2])

    def test_repeated_offset_page_stops_safely(self):
        calls = []

        def fake_get(url, params, headers, timeout):
            calls.append(dict(params))
            return FakeResponse({"jobs": jobs(1, 100), "totalCount": 200})

        scrape = {
            "api_url": "https://example.com/api/jobs",
            "job_url_prefix": "https://example.com/jobs/",
        }
        with patch("app.api.ats.jobsapi.requests.get", side_effect=fake_get):
            result, error = fetch_jobsapi(scrape, {}, "Example")
        self.assertIn("partial", error)
        self.assertEqual(len(result), 100)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()

import unittest
import json
from unittest.mock import patch

from app.api.ats.icims import fetch_icims


class _Response:
    def __init__(self, text, url, status_code=200, content_type="text/html"):
        self.text = text
        self.url = url
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self.content = text.encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return json.loads(self.text)


def _job(job_id, title):
    slug = title.lower().replace(" ", "-")
    return (
        f'<a class="iCIMS_Anchor" '
        f'href="https://careers-example.icims.com/jobs/{job_id}/{slug}/job">'
        f"{title}</a>"
    )


class IcimsPaginationTests(unittest.TestCase):
    @patch("app.api.ats.icims.requests.get")
    def test_jibe_public_api_is_parsed(self, get):
        payload = {
            "totalCount": 1,
            "jobs": [{"data": {
                "slug": "6172",
                "req_id": "6172",
                "title": "Certified Medical Assistant",
                "searchable": True,
                "full_location": "Columbus, Ohio",
                "posted_date": "2026-08-25T18:09:00+0000",
                "employment_type": "PART_TIME",
                "description": "<p>Patient care</p>",
                "apply_url": "https://careers-armorhealth.icims.com/jobs/6172/login",
                "meta_data": {"canonical_url": "https://careers.armorhealthcare.com/jobs/6172?lang=en-us"},
            }}],
        }
        get.return_value = _Response(
            json.dumps(payload),
            "https://careers.armorhealthcare.com/api/jobs?limit=1000",
            content_type="application/json",
        )

        jobs, error = fetch_icims(
            {"icims_slug": "https://careers.armorhealthcare.com"},
            {},
            "Armor Health",
        )

        self.assertIsNone(error)
        self.assertEqual(1, len(jobs))
        self.assertEqual("6172", jobs[0]["ats_id"])
        self.assertEqual("Columbus, OH, US", jobs[0]["location"])

    @patch("app.api.ats.icims.requests.get")
    def test_search_fallback_paginates_until_no_new_ids(self, get):
        def response(url, **_kwargs):
            if url.endswith("/sitemap.xml"):
                return _Response("", url, 404)
            if "pr=0" in url:
                return _Response(_job(101, "First Engineer"), url)
            if "pr=1" in url:
                return _Response(_job(202, "Second Engineer"), url)
            return _Response("", url)

        get.side_effect = response
        jobs, error = fetch_icims(
            {
                "icims_slug": "example",
                "fetch_details": False,
                "allow_browser_fallback": False,
            },
            {},
            "Example",
        )

        self.assertIsNone(error)
        self.assertEqual({"101", "202"}, {job["ats_id"] for job in jobs})
        requested = [call.args[0] for call in get.call_args_list]
        self.assertTrue(any("pr=2" in url for url in requested))


if __name__ == "__main__":
    unittest.main()

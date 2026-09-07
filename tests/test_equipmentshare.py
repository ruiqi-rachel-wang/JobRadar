import unittest
from unittest.mock import patch

from app.api.ats.equipmentshare import fetch_equipmentshare


class _Response:
    status_code = 200

    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


def _job(job_id, title, prospect="false"):
    return {
        "absolute_url": (
            "https://www.equipmentshare.com/careers/openings/"
            f"{job_id}/{title.lower().replace(' ', '-')}"
        ),
        "job_id": job_id,
        "google": {
            "job": {
                "title": title,
                "requisitionId": job_id,
                "addresses": ["Columbia, MO"],
                "employmentTypes": ["FULL_TIME"],
                "postingPublishTime": {
                    "seconds": "1785342980",
                    "nanos": 0,
                },
                "customAttributes": {
                    "is_prospect_job": {
                        "stringValues": [prospect],
                    },
                    "work_environment": {
                        "stringValues": ["Hybrid"],
                    },
                    "job_description": {
                        "stringValues": [
                            "<h2>Requirements</h2><ul><li>Python</li></ul>"
                        ],
                    },
                },
            },
        },
    }


class EquipmentShareTests(unittest.TestCase):
    @patch("app.api.ats.equipmentshare.requests.get")
    def test_paginates_first_party_api_and_excludes_future_interest(self, get):
        get.side_effect = [
            _Response({
                "metadata": {"total": 4},
                "jobs": [
                    _job("101", "Platform Engineer"),
                    _job("102", "Future Technician", prospect="true"),
                    _job("104", "Future Territory Account Manager"),
                ],
            }),
            _Response({
                "metadata": {"total": 4},
                "jobs": [_job("103", "Data Engineer")],
            }),
        ]

        jobs, error = fetch_equipmentshare(
            {
                "api_url": "https://example.test/jobs",
                "page_size": 2,
            },
            {},
            "EquipmentShare",
        )

        self.assertIsNone(error)
        self.assertEqual(
            {"Platform Engineer", "Data Engineer"},
            {job["title"] for job in jobs},
        )
        self.assertEqual("Columbia, MO, US", jobs[0]["location"])
        self.assertEqual("Hybrid", jobs[0]["workplace_type"])
        self.assertEqual("2026-07-29T16:36:20Z", jobs[0]["posted_at"])
        self.assertIn("<h2>Requirements</h2>", jobs[0]["description_raw"])
        self.assertEqual(2, get.call_count)

    @patch("app.api.ats.equipmentshare.requests.get")
    def test_incomplete_source_total_returns_protective_error(self, get):
        get.return_value = _Response({
            "metadata": {"total": 5},
            "jobs": [_job("101", "Platform Engineer")],
        })

        jobs, error = fetch_equipmentshare(
            {
                "api_url": "https://example.test/jobs",
                "page_size": 2,
                "hard_max_pages": 1,
            },
            {},
            "EquipmentShare",
        )

        self.assertEqual(1, len(jobs))
        self.assertEqual(
            "EquipmentShare partial listing: fetched 1 of 5 source records",
            error,
        )


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

from app.api.ats.darwinbox import fetch_darwinbox


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _job(number):
    return {
        "id": str(number),
        "internal_job_code": f"REQ-{number}",
        "designation_display_name": f"Engineer {number}",
        "emp_type_name": "Permanent",
        "experience_from": 5,
        "experience_to": 7,
        "jd": "<h2>Requirements</h2><ul><li>Build systems</li></ul>",
        "posted_on": 1785283200,
        "created_on": "2026-07-29T00:00:00Z",
        "is_remote": False,
        "department_name": "Engineering",
        "functional_area_name": "Platform",
        "tool_tip_locations": [
            "San Francisco, California, United States",
            "New York, New York, United States",
        ],
        "salary_range": "$1-$2",
    }


class DarwinboxTests(unittest.TestCase):
    @patch("app.api.ats.darwinbox.requests.post")
    def test_fetches_all_pages_and_keeps_raw_without_salary(self, post):
        def page_response(_url, **kwargs):
            page = kwargs["json"]["page"]
            items = (
                [_job(number) for number in range(1, 11)]
                if page == 1
                else [_job(11)]
                if page == 2
                else []
            )
            return _Response({
                "status": "success",
                "job_counts": 11,
                "data": items,
            })

        post.side_effect = page_response
        jobs, error = fetch_darwinbox(
            {
                "darwinbox_tenant": "example.com",
                "page_size": 10,
            },
            {},
            "Example",
        )

        self.assertIsNone(error)
        self.assertEqual(11, len(jobs))
        first = jobs[0]
        self.assertEqual("1", first["ats_id"])
        self.assertEqual("REQ-1", first["requisition_id"])
        self.assertEqual("5–7 years", first["exp_label"])
        self.assertEqual("provider", first["exp_source"])
        self.assertEqual("Permanent", first["employment_type_raw"])
        self.assertEqual("onsite", first["workplace_type"])
        self.assertEqual(2, len(first["locations_raw"]))
        self.assertIn("<h2>Requirements</h2>", first["description_raw"])
        self.assertNotIn("salary_range", first["raw_data"])


if __name__ == "__main__":
    unittest.main()

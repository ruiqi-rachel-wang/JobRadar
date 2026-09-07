import unittest
from unittest.mock import Mock, patch

from app.api.ats.tiktok import fetch_tiktok


class TikTokTests(unittest.TestCase):
    @patch("app.api.ats.tiktok.requests.post")
    def test_maps_public_careers_response(self, mock_post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "code": 0,
            "data": {
                "count": 1,
                "job_post_list": [{
                    "id": "7595445244009138485",
                    "code": "A19736A",
                    "title": "Software Engineer - AI Search Infrastructure",
                    "description": "Build scalable search systems.",
                    "requirement": "Five years of engineering experience.",
                    "recruit_type": {"en_name": "Regular"},
                    "job_category": {"en_name": "R&D"},
                    "city_info": {
                        "en_name": "San Jose",
                        "parent": {
                            "en_name": "California",
                            "parent": {"en_name": "United States of America"},
                        },
                    },
                }],
            },
        }
        mock_post.return_value = response

        jobs, error = fetch_tiktok({}, set(), "TikTok")

        self.assertIsNone(error)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["ats_id"], "7595445244009138485")
        self.assertEqual(jobs[0]["location"], "San Jose, California, United States of America")
        self.assertEqual(jobs[0]["department"], "R&D")
        self.assertIn("Five years", jobs[0]["description"])

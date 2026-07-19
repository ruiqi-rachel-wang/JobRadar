import unittest

from app.api.main import app


class SecurityHardeningTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def test_https_responses_include_browser_security_headers(self):
        response = self.client.get("/", base_url="https://jobdrop.site")

        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(
            response.headers["Referrer-Policy"],
            "strict-origin-when-cross-origin",
        )
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertEqual(
            response.headers["Strict-Transport-Security"],
            "max-age=31536000; includeSubDomains",
        )

    def test_cross_site_write_is_rejected(self):
        response = self.client.post(
            "/login",
            base_url="https://jobdrop.site",
            headers={
                "Origin": "https://attacker.example",
                "Sec-Fetch-Site": "cross-site",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "Cross-site request rejected.")
        self.assertEqual(self.client.get("/logout").status_code, 405)


if __name__ == "__main__":
    unittest.main()

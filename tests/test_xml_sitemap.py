import unittest
from unittest.mock import patch

from app.api.ats.xml_feed import fetch_xml_feed


class _Response:
    content = b"""<?xml version='1.0'?>
    <urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
      <url>
        <loc>https://careers.example/jobs/results/123-senior-software-engineer/</loc>
        <lastmod>2026-07-10T08:39:12Z</lastmod>
      </url>
      <url><loc>https://unrelated.example/jobs/456-ignore-me/</loc></url>
    </urlset>"""

    def raise_for_status(self):
        return None


class SitemapFeedTests(unittest.TestCase):
    @patch("app.api.ats.xml_feed.requests.get", return_value=_Response())
    def test_sitemap_urls_become_jobs_without_misusing_lastmod(self, _get):
        jobs, error = fetch_xml_feed(
            {
                "list_url": "https://careers.example/jobs/sitemap",
                "feed_format": "sitemap",
                "include": "careers.example/jobs/results/",
            },
            {},
            "Example",
        )

        self.assertIsNone(error)
        self.assertEqual(1, len(jobs))
        self.assertEqual("Senior Software Engineer", jobs[0]["title"])
        self.assertEqual("", jobs[0]["posted"])

    @patch("app.api.ats.xml_feed.requests.get")
    def test_rss_items_become_jobs(self, get):
        response = _Response()
        response.content = b"""<?xml version='1.0'?>
        <rss><channel><item>
          <title>Design Manager</title>
          <link>https://careers.example/JobDetail/Design-Manager/123</link>
          <pubDate>Tue, 16 Jun 2026 00:00:00 +0000</pubDate>
          <description>Current opening</description>
        </item></channel></rss>"""
        get.return_value = response

        jobs, error = fetch_xml_feed(
            {
                "list_url": "https://careers.example/feed/",
                "feed_format": "rss",
                "include": "careers.example/JobDetail/",
            },
            {},
            "Example",
        )

        self.assertIsNone(error)
        self.assertEqual(1, len(jobs))
        self.assertEqual("Design Manager", jobs[0]["title"])
        self.assertEqual("2026-06-16", jobs[0]["posted"])


if __name__ == "__main__":
    unittest.main()

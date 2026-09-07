import unittest
from unittest.mock import Mock, patch

from app.api.ats.xml_feed import fetch_xml_feed


class XmlFeedTests(unittest.TestCase):
    @patch("app.api.ats.xml_feed.requests.get")
    def test_rss_preserves_extended_job_fields(self, mock_get):
        response = Mock()
        response.content = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel><item>
          <title>Software Engineer (37301-DCOL)</title>
          <title_raw>Software Engineer</title_raw>
          <id>37301</id>
          <link>https://www.mathworks.com/company/jobs/opportunities/37301-software-engineer.html</link>
          <summary><![CDATA[<p>Build MATLAB systems.</p>]]></summary>
          <responsibilities><![CDATA[<ul><li>Ship software.</li></ul>]]></responsibilities>
          <location>
            <locationName>US-MA-Natick</locationName>
            <city>Natick</city><state>MA</state><country>US</country>
          </location>
          <job_function>Product Development</job_function>
        </item></channel></rss>"""
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        jobs, error = fetch_xml_feed(
            {
                "list_url": "https://www.mathworks.com/company/jobs/opportunities/rss.xml",
                "include": "mathworks.com/company/jobs/opportunities/",
            },
            set(),
            "MathWorks",
        )

        self.assertIsNone(error)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Software Engineer")
        self.assertEqual(jobs[0]["ats_id"], "37301")
        self.assertEqual(jobs[0]["location"], "Natick, MA, US")
        self.assertEqual(jobs[0]["department"], "Product Development")
        self.assertIn("Ship software", jobs[0]["description"])


if __name__ == "__main__":
    unittest.main()

import unittest

from app.api.common import _parse_robots_groups, _robots_can_fetch


UA = "JobDropBot/0.1 (+https://github.com/ruiqi-rachel-wang/JobRadar)"


class RobotsRulesTests(unittest.TestCase):
    def allowed(self, text, url):
        return _robots_can_fetch(_parse_robots_groups(text), UA, url)

    def test_more_specific_allow_overrides_root_disallow(self):
        text = "User-agent: *\nDisallow: /\nAllow: /careers\n"
        self.assertTrue(self.allowed(text, "https://example.com/careers"))
        self.assertTrue(self.allowed(text, "https://example.com/careers/job/123"))
        self.assertFalse(self.allowed(text, "https://example.com/private"))

    def test_query_specific_disallow_does_not_block_plain_careers_path(self):
        text = (
            "User-agent: *\n"
            "Disallow: /careers?\n"
            "Disallow: /*?utm_source=\n"
            "Allow: /\n"
        )
        self.assertTrue(self.allowed(text, "https://example.com/careers"))
        self.assertFalse(self.allowed(text, "https://example.com/careers?source=menu"))

    def test_wildcard_allow_and_more_specific_query_block(self):
        text = (
            "User-agent: *\n"
            "Allow: /*/careers\n"
            "Disallow: /*/careers/*qtvc=\n"
            "Disallow: /\n"
        )
        self.assertTrue(self.allowed(text, "https://example.com/en_US/careers/SearchJobs"))
        self.assertFalse(self.allowed(text, "https://example.com/en_US/careers/job?qtvc=x"))

    def test_specific_bot_group_beats_wildcard(self):
        text = (
            "User-agent: JobDropBot\nDisallow: /private\n\n"
            "User-agent: *\nAllow: /\n"
        )
        self.assertFalse(self.allowed(text, "https://example.com/private"))
        self.assertTrue(self.allowed(text, "https://example.com/public"))

    def test_allow_wins_equal_specificity_tie(self):
        text = "User-agent: *\nDisallow: /careers\nAllow: /careers\n"
        self.assertTrue(self.allowed(text, "https://example.com/careers"))


if __name__ == "__main__":
    unittest.main()

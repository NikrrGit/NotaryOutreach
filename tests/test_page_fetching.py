"""Website extraction and download diagnostics without network requests."""

from email.message import Message
import unittest
from unittest.mock import Mock, patch

from notaryoutreach.verification import fetch_page_text


class PageFetchingTests(unittest.TestCase):
    def test_follows_page_anchors_not_metadata_and_resolves_redirects(self):
        response = Mock()
        response.headers = Message()
        response.headers["content-type"] = "text/html; charset=utf-8"
        response.geturl.return_value = "https://office.example/activities/"
        response.read.return_value = b'''
            <link rel="alternate" href="/wp-json/oembed?url=unternehmen">
            <link href="/wp-json/oembed?url=unternehmen&amp;format=xml">
            <script>var template = '<a href="/unternehmen-fake">Fake</a>';</script>
            <a href="unternehmen">Company formation</a>
            <a href="unternehmen#top">Same page</a>
            <a href="/wp-json/oembed?url=unternehmen">API</a>
            <a href="https://other.example/unternehmen">External</a>
            <a href="https://user:secret@office.example/unternehmen">Invalid</a>
            <a href="mailto:unternehmen@office.example">Email</a>
            <a href="/gmbh.pdf">PDF</a>
            <a href="/portfolio">Investments</a>
        '''
        with patch("notaryoutreach.verification.urlopen") as fetch:
            fetch.return_value.__enter__.return_value = response
            links = []
            text = fetch_page_text("https://office.example", links)
            self.assertIn("Company formation", text)
            self.assertNotIn("var template", text)
            self.assertEqual(links, ["https://office.example/activities/unternehmen", "https://office.example/gmbh.pdf"])
            links = []
            fetch_page_text("https://office.example", links, research_pattern="portfolio")
            self.assertEqual(links, ["https://office.example/portfolio"])
            response.headers.replace_header("content-type", "text/plain")
            links = []
            fetch_page_text("https://office.example", links)
            self.assertEqual(links, [])

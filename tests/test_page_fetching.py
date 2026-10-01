"""Website extraction and download diagnostics without network requests."""

from email.message import Message
import ssl
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from agents.discovery import Candidate
from agents.verification import VerificationAgent
from notaryoutreach.verification import MAX_PAGE_BYTES, PageFetchError, fetch_page_text


class PageFetchingTests(unittest.TestCase):
    def test_fetch_failures_keep_safe_diagnostics_through_verification(self):
        url = "https://office.example"
        cases = [(HTTPError(url, status, "private-response", {}, None), expected) for status, expected in (
            (403, "blocked automated access"), (404, "not found"), (429, "limiting requests"), (500, "HTTP 500"),
        )]
        cases.extend([
            (URLError(ssl.SSLCertVerificationError("private-response")), "secure connection"),
            (URLError(TimeoutError("private-response")), "timed out"),
            (URLError("private-response"), "could not be reached"),
        ])
        office = Candidate(name="Office", city="Berlin", website=url, source_url=url)
        for exception, expected in cases:
            with self.subTest(expected=expected), patch("notaryoutreach.verification.urlopen", side_effect=exception):
                result = VerificationAgent(provider=Mock()).verify(office, "UG")
                self.assertEqual(result.status, "unknown")
                self.assertEqual(len(result.errors), 1)
                self.assertIn(expected, result.errors[0].message)
                self.assertNotIn("private-response", result.errors[0].message)
                self.assertEqual(result.errors[0].source_url, url)
        for mime, body, expected in (("application/json", b"{}", "unsupported file type"),
                                     ("text/html", b"x" * (MAX_PAGE_BYTES + 1), "download limit")):
            with self.subTest(mime=mime), patch("notaryoutreach.verification.urlopen") as fetch:
                response = fetch.return_value.__enter__.return_value
                response.headers = Message()
                response.headers["content-type"] = mime
                response.read.return_value = body
                with self.assertRaisesRegex(PageFetchError, expected):
                    fetch_page_text(url)

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

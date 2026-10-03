"""Streamlit search and editable emails work with search-only credentials."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from agents.discovery import Candidate
from services.outreach_service import OutreachService
from storage.sqlite import SQLiteStorage


class TavilyUITests(unittest.TestCase):
    def test_search_template_save_and_mail_app_work_without_model_credentials(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            storage = SQLiteStorage(path / "data.db")
            discovery = Mock()
            discovery.discover.return_value = [Candidate(name="Notar Example", city="Berlin",
                email="office@example.org", source_url="https://example.org")]
            service = OutreachService(storage, env_file=path / "missing.env", checkpoint_path=path / "checkpoint.db",
                                      discovery_agent=discovery)
            values = {"LLM_PROVIDER": "none", "SEARCH_PROVIDER": "tavily", "TAVILY_API_KEY": "secret",
                      "DATABASE_PATH": str(storage.path)}
            with patch.dict("os.environ", values, clear=True), patch("services.outreach_service.OutreachService", return_value=service):
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "src/ui/app.py"), default_timeout=15).run()
                next(widget for widget in app.text_input if widget.label == "Location").set_value("Berlin")
                next(button for button in app.button if button.label == "Start search").click().run()
                self.assertFalse(app.exception)
                self.assertFalse(app.error)
                self.assertTrue(any("Search finished" in item.value for item in app.success))
                job = service.list_jobs()[0]
                candidate = service.load_results(job["id"])["candidates"][0]
                template = f"template-{candidate['id']}"
                self.assertIn("UG", app.text_area(key=f"body-{template}").value)
                app.text_area(key=f"body-{template}").set_value("Guten Tag, wann wäre ein Termin möglich?\nTest Sender")
                app.button(key=f"edit-{template}").click().run()
                self.assertFalse(app.exception)
                self.assertFalse(app.error)
                draft = service.load_results(job["id"])["drafts"][0]
                self.assertTrue(app.button(key=f"evaluate-{draft['id']}").disabled)
                link = next(item for item in app.get("link_button") if item.proto.label == "Open in email app")
                self.assertIn("mailto:office@example.org", link.proto.url)
                self.assertFalse(link.proto.disabled)

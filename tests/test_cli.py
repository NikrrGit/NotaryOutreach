"""CLI dispatch and validation without agent calls."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from notaryoutreach.main import main


class CLITests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.service.create_job.return_value = "job-1"
        self.service.run_job.return_value = {"job": {"id": "job-1", "status": "ready_for_review"}}
        self.service.resume_job.return_value = self.service.run_job.return_value
        self.service.load_results.return_value = self.service.run_job.return_value
        self.service.list_jobs.return_value = [{"id": "job-1"}]
        self.factory = patch("notaryoutreach.main.OutreachService", return_value=self.service).start()
        self.storage = patch("notaryoutreach.main.SQLiteStorage").start()
        self.environment = patch("notaryoutreach.main.dotenv_values", return_value={}).start()
        self.addCleanup(patch.stopall)

    def run_cli(self, arguments):
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = main(arguments)
        return code, output.getvalue(), errors.getvalue()

    def test_search_passes_both_target_settings_to_service(self):
        cases = [
            (["--type", "notary", "--location", " Berlin ", "--company-type", "UG"],
             dict(target_type="notary", location="Berlin", company_type="UG", startup_description=None,
                  industry=None, funding_stage=None, target_count=10)),
            (["--type", "vc", "--location", "Europe", "--description", "Security software",
              "--industry", "Cybersecurity", "--stage", "Seed", "--limit", "20"],
             dict(target_type="vc", location="Europe", company_type=None, startup_description="Security software",
                  industry="Cybersecurity", funding_stage="Seed", target_count=20)),
        ]
        for arguments, expected in cases:
            with self.subTest(target=expected["target_type"]):
                self.service.reset_mock()
                code, output, errors = self.run_cli(["search", *arguments])
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(output)["job"]["id"], "job-1")
                self.assertIn("Saved job: job-1", errors)
                self.service.create_job.assert_called_once_with(**expected)
                self.service.run_job.assert_called_once_with("job-1")

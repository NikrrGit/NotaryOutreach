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

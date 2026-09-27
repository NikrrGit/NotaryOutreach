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

    def test_invalid_inputs_fail_before_creating_storage_or_jobs(self):
        cases = [
            ["search", "--type", "notary", "--location", "Berlin"],
            ["search", "--type", "vc", "--location", "Europe"],
            ["search", "--type", "notary", "--location", " ", "--company-type", "UG"],
            ["search", "--type", "notary", "--location", "Berlin", "--company-type", "UG", "--limit", "0"],
            ["search", "--type", "notary", "--location", "Berlin", "--company-type", "UG", "--industry", "AI"],
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit) as caught:
                self.run_cli(arguments)
            self.assertEqual(caught.exception.code, 2)
        self.storage.assert_not_called()
        self.service.create_job.assert_not_called()

    def test_saved_job_commands_and_path_precedence(self):
        self.environment.return_value = {"DATABASE_PATH": "file.db", "CHECKPOINT_PATH": "file-checkpoints.db"}
        with patch.dict("os.environ", {"DATABASE_PATH": "env.db", "CHECKPOINT_PATH": "env-checkpoints.db"}, clear=True):
            for command, method in (("run", "run_job"), ("resume", "resume_job"), ("show", "load_results")):
                with self.subTest(command=command):
                    self.service.reset_mock()
                    code, output, errors = self.run_cli([
                        "--database", "cli.db", command, "job-1", "--checkpoint-path", "cli-checkpoints.db",
                        "--env-file", "custom.env",
                    ])
                    self.assertEqual(code, 0)
                    self.assertEqual(json.loads(output)["job"]["id"], "job-1")
                    self.assertEqual(errors, "")
                    getattr(self.service, method).assert_called_once_with("job-1")
                    self.service.create_job.assert_not_called()
                    self.storage.assert_called_with(Path("cli.db"))
                    self.environment.assert_called_with(Path("custom.env"))
                    self.assertEqual(self.factory.call_args.kwargs["checkpoint_path"], Path("cli-checkpoints.db"))
            code, output, _ = self.run_cli(["jobs"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output), [{"id": "job-1"}])
            self.storage.assert_called_with("env.db")
            self.assertEqual(self.factory.call_args.kwargs["checkpoint_path"], "env-checkpoints.db")
        with patch.dict("os.environ", {}, clear=True):
            self.run_cli(["jobs"])
            self.storage.assert_called_with("file.db")

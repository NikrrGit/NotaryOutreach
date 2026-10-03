"""Provider diagnostics identify billing blocks without exposing response bodies."""

import unittest

from providers.errors import failure_message


class ProviderErrorTests(unittest.TestCase):
    def test_billing_rejections_are_actionable_and_private(self):
        detail = "Your team private-team has either used all available credits or reached its monthly spending limit."
        for body in (detail, {"message": detail}, {"error": {"message": detail}}):
            with self.subTest(body=body):
                error = RuntimeError("private exception")
                error.status_code = 403
                error.body = body
                wrapped = RuntimeError("wrapper")
                wrapped.__cause__ = error
                message = failure_message(wrapped)
                self.assertIn("Add credits or raise the spending limit", message)
                self.assertNotIn("private", message)

    def test_other_forbidden_responses_do_not_claim_billing_failure(self):
        for body in (None, {}, {"error": {"message": "Model access denied"}}, {"error": []}):
            error = RuntimeError("private exception")
            error.status_code = 403
            error.body = body
            self.assertIn("Check API key and model permissions", failure_message(error))

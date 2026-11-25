import json
import os
import tempfile
import unittest
from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import main


class FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text or json.dumps(payload)

    def json(self):
        return self._payload


class FakeMailbox:
    def __init__(self, raw_messages):
        self.raw_messages = raw_messages
        self.logged_in = False
        self.logged_out = False

    def login(self, email, password):
        self.logged_in = True
        return "OK", []

    def select(self, mailbox, readonly=False):
        return "OK", [b""]

    def search(self, charset, *criteria):
        ids = b" ".join(str(i + 1).encode() for i in range(len(self.raw_messages)))
        return "OK", [ids]

    def fetch(self, message_id, query):
        index = int(message_id) - 1
        return "OK", [(b"RFC822", self.raw_messages[index]), b")"]

    def logout(self):
        self.logged_out = True
        return "BYE", []


def build_linkedin_email(link, title="Junior QA Engineer"):
    message = EmailMessage()
    message["From"] = "LinkedIn Jobs <jobs-noreply@linkedin.com>"
    message["Subject"] = "New jobs matching your search"
    message.set_content("You have new job recommendations. Open LinkedIn to view them.")
    message.add_alternative(
        f'<html><body><a href="{link}">{title}</a></body></html>',
        subtype="html",
    )
    return message.as_bytes()


class TestJobExtraction(unittest.TestCase):
    def test_extracts_jobs_from_nested_payload_and_deduplicates(self):
        payload = {
            "data": {
                "jobs": [
                    {"job_id": "job-1", "job_title": "QA Engineer"},
                    {"job_id": "job-1", "job_title": "Duplicate"},
                    {"job_id": "job-2", "job_title": "SOC Analyst"},
                ]
            }
        }
        jobs = main.extract_job_list(payload)
        self.assertEqual([job["job_id"] for job in jobs], ["job-1", "job-2"])

    def test_extracts_json_encoded_payload(self):
        payload = json.dumps({"results": [{"job_id": "job-3", "job_title": "Support Engineer"}]})
        jobs = main.extract_job_list(payload)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["job_title"], "Support Engineer")

    def test_ignores_malformed_json_and_non_job_values(self):
        self.assertEqual(main.extract_job_list('{"data": invalid-json'), [])
        self.assertEqual(main.extract_job_list({"status": "ok", "message": "no jobs"}), [])


class TestLinkedInJobAlerts(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_cwd = os.getcwd()
        os.chdir(self.temp_dir.name)
        main.LINKEDIN_PENDING_LINKS.clear()

    def tearDown(self):
        main.LINKEDIN_PENDING_LINKS.clear()
        os.chdir(self.original_cwd)
        self.temp_dir.cleanup()

    def test_imports_linkedin_job_alert_and_persists_deduplication(self):
        link = "https://www.linkedin.com/jobs/view/987654321/"
        raw = build_linkedin_email(link)
        mailbox = FakeMailbox([raw])
        env = {
            "LINKEDIN_ALERT_EMAIL": "alerts@example.com",
            "LINKEDIN_ALERT_APP_PASSWORD": "abcdefghijklmnop",
        }
        with patch.dict(os.environ, env, clear=True), patch(
            "main.imaplib.IMAP4_SSL", return_value=mailbox
        ):
            jobs = main.fetch_linkedin_alert_jobs()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["job_title"], "Junior QA Engineer")
        self.assertEqual(jobs[0]["job_apply_link"], link)
        self.assertEqual(jobs[0]["job_publisher"], "LinkedIn Job Alert Email")
        self.assertFalse(jobs[0]["job_apply_is_direct"])

        main.save_linkedin_alert_state()
        with open(".linkedin_alert_state.json", encoding="utf-8") as state_file:
            saved = json.load(state_file)
        self.assertIn(link, saved["seen_links"])

        # The same message is not re-imported after state has been saved.
        mailbox2 = FakeMailbox([raw])
        with patch.dict(os.environ, env, clear=True), patch(
            "main.imaplib.IMAP4_SSL", return_value=mailbox2
        ):
            second_pass = main.fetch_linkedin_alert_jobs()
        self.assertEqual(second_pass, [])

    def test_ignores_non_linkedin_email(self):
        message = EmailMessage()
        message["From"] = "alerts@example.com"
        message["Subject"] = "New job"
        message.set_content("A job listing")
        message.add_alternative(
            '<a href="https://www.linkedin.com/jobs/view/111/">QA Engineer</a>',
            subtype="html",
        )
        mailbox = FakeMailbox([message.as_bytes()])
        with patch.dict(os.environ, {
            "LINKEDIN_ALERT_EMAIL": "alerts@example.com",
            "LINKEDIN_ALERT_APP_PASSWORD": "abcdefghijklmnop",
        }, clear=True), patch("main.imaplib.IMAP4_SSL", return_value=mailbox):
            self.assertEqual(main.fetch_linkedin_alert_jobs(), [])

    def test_does_not_reuse_sender_password_for_different_alert_inbox(self):
        env = {
            "LINKEDIN_ALERT_EMAIL": "alerts@example.com",
            "EMAIL_SENDER": "sender@example.com",
            "EMAIL_APP_PASSWORD": "abcdefghijklmnop",
        }
        with patch.dict(os.environ, env, clear=True), patch(
            "main.imaplib.IMAP4_SSL"
        ) as imap_factory:
            self.assertEqual(main.fetch_linkedin_alert_jobs(), [])
        imap_factory.assert_not_called()

    def test_skips_cleanly_when_credentials_are_missing(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "main.imaplib.IMAP4_SSL"
        ) as imap_factory:
            self.assertEqual(main.fetch_linkedin_alert_jobs(), [])
        imap_factory.assert_not_called()


class TestJobCollectionAndSelection(unittest.TestCase):
    def test_fetch_jobs_combines_jsearch_and_linkedin_alerts(self):
        jsearch_job = {"job_id": "j-1", "job_title": "QA Engineer"}
        linkedin_job = {
            "job_id": "li-1",
            "job_title": "SOC Analyst",
            "job_publisher": "LinkedIn Job Alert Email",
            "job_apply_link": "https://www.linkedin.com/jobs/view/123/",
        }
        with patch.dict(os.environ, {"RAPIDAPI_KEY": "test-key"}, clear=True), patch.object(
            main, "SEARCH_QUERIES", ["QA Engineer"]
        ), patch("main.requests.get", return_value=FakeResponse({"data": [jsearch_job]})), patch(
            "main.fetch_linkedin_alert_jobs", return_value=[linkedin_job]
        ):
            jobs = main.fetch_jobs()

        self.assertEqual({job["job_id"] for job in jobs}, {"j-1", "li-1"})

    def test_jsearch_stops_after_monthly_quota_exhaustion_and_uses_backup(self):
        quota_response = FakeResponse({}, status_code=429, text="You have exceeded the MONTHLY quota for Requests")
        backup_response = FakeResponse({"jobs": []})
        with patch.dict(os.environ, {"RAPIDAPI_KEY": "test-key"}, clear=True), patch.object(
            main, "SEARCH_QUERIES", ["one", "two", "three"]
        ), patch("main.requests.get", side_effect=[quota_response] + [backup_response] * 10) as get, patch(
            "main.fetch_linkedin_alert_jobs", return_value=[]
        ):
            main.fetch_jobs()
        self.assertEqual(get.call_count, 11)  # one JSearch request + ten Remotive backup queries

    def test_jsearch_results_are_deduplicated_across_queries(self):
        jsearch_job = {"job_id": "same-id", "job_title": "QA Engineer"}
        with patch.dict(os.environ, {"RAPIDAPI_KEY": "test-key"}, clear=True), patch.object(
            main, "SEARCH_QUERIES", ["query-one", "query-two"]
        ), patch("main.requests.get", return_value=FakeResponse({"data": [jsearch_job]})) as get, patch(
            "main.fetch_linkedin_alert_jobs", return_value=[]
        ):
            jobs = main.fetch_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(get.call_count, 2)

    def test_remotive_backup_runs_when_jsearch_key_is_missing(self):
        remotive_job = {
            "id": 77,
            "title": "Remote QA Tester",
            "company_name": "Example Co",
            "candidate_required_location": "Worldwide",
            "description": "Manual testing role",
            "url": "https://example.com/jobs/77",
        }
        with patch.dict(os.environ, {}, clear=True), patch(
            "main.requests.get", return_value=FakeResponse({"jobs": [remotive_job]})
        ) as get, patch("main.fetch_linkedin_alert_jobs", return_value=[]):
            jobs = main.fetch_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["job_title"], "Remote QA Tester")
        self.assertTrue(jobs[0]["job_is_remote"])
        self.assertEqual(get.call_count, 10)

    def test_linkedin_jobs_get_slots_in_gemini_input(self):
        ordinary = [
            {"job_id": f"api-{i}", "job_title": "API job"}
            for i in range(60)
        ]
        linkedin = [
            {
                "job_id": f"linkedin-{i}",
                "job_title": f"LinkedIn job {i}",
                "job_publisher": "LinkedIn Job Alert Email",
            }
            for i in range(20)
        ]
        selected = main.select_jobs_for_evaluation(ordinary + linkedin)
        self.assertEqual(len(selected), 50)
        self.assertEqual(
            sum(job.get("job_publisher") == "LinkedIn Job Alert Email" for job in selected),
            15,
        )

    def test_selection_returns_all_jobs_when_under_limit(self):
        jobs = [
            {"job_id": "li", "job_publisher": "LinkedIn Job Alert Email"},
            {"job_id": "api", "job_publisher": "JSearch"},
        ]
        self.assertEqual(main.select_jobs_for_evaluation(jobs), jobs)


class TestGeminiAndEmail(unittest.TestCase):
    def test_gemini_fallback_tries_next_model(self):
        expected = SimpleNamespace(text="<html>digest</html>")
        client = MagicMock()
        client.models.generate_content.side_effect = [RuntimeError("model unavailable"), expected]
        response = main.generate_with_fallback(client, "test prompt")
        self.assertIs(response, expected)
        self.assertEqual(client.models.generate_content.call_count, 2)

    def test_digest_falls_back_when_all_gemini_models_are_unavailable(self):
        client = MagicMock()
        client.models.generate_content.side_effect = RuntimeError("503 UNAVAILABLE: temporary model overload")
        jobs = [{
            "job_id": "qa-1",
            "job_title": "Junior QA Tester",
            "employer_name": "Example Pharma",
            "job_city": "Vadodara",
            "job_description": "Manual testing, regression testing and test case execution.",
            "job_apply_link": "https://example.com/jobs/qa-1",
            "job_publisher": "Remotive",
        }]
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=True), patch(
            "main.genai.Client", return_value=client
        ):
            digest = main.evaluate_with_gemini(jobs)
        self.assertIn("Gemini temporarily unavailable", digest)
        self.assertIn("Junior QA Tester", digest)
        self.assertIn("View Job / Apply", digest)
        self.assertIn("https://example.com/jobs/qa-1", digest)

    def test_digest_falls_back_when_gemini_key_is_missing(self):
        jobs = [{"job_title": "QA Tester", "job_description": "Manual testing"}]
        with patch.dict(os.environ, {}, clear=True):
            digest = main.evaluate_with_gemini(jobs)
        self.assertIn("Morning IT Job Digest", digest)
        self.assertIn("QA Tester", digest)

    def test_gemini_input_contains_prioritized_linkedin_job(self):
        class FakeModels:
            def __init__(self):
                self.prompt = None

            def generate_content(self, model, contents):
                self.prompt = contents
                return SimpleNamespace(text="<html>digest</html>")

        fake_client = SimpleNamespace(models=FakeModels())
        jobs = [
            {"job_id": f"api-{i}", "job_title": "API QA", "job_description": "testing"}
            for i in range(60)
        ] + [
            {
                "job_id": f"li-{i}",
                "job_title": f"LinkedIn Alert {i}",
                "job_publisher": "LinkedIn Job Alert Email",
                "job_apply_link": f"https://www.linkedin.com/jobs/view/{i}/",
            }
            for i in range(20)
        ]
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=True), patch(
            "main.genai.Client", return_value=fake_client
        ):
            html = main.evaluate_with_gemini(jobs)

        self.assertEqual(html, "<html>digest</html>")
        self.assertIn("LinkedIn Alert 0", fake_client.models.prompt)
        self.assertNotIn("LinkedIn Alert 19", fake_client.models.prompt)

    def test_email_uses_starttls_and_deduplicates_cc(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        env = {
            "EMAIL_SENDER": "sender@example.com",
            "EMAIL_APP_PASSWORD": "abcdefghijklmnop",
            "EMAIL_RECEIVER": "receiver@example.com",
            "EMAIL_CC": "receiver@example.com",
        }
        with patch.dict(os.environ, env, clear=True), patch(
            "main.smtplib.SMTP", return_value=connection
        ) as smtp, patch("main.smtplib.SMTP_SSL") as smtp_ssl:
            main.send_email_digest("<html>test</html>")

        smtp.assert_called_once()
        smtp_ssl.assert_not_called()
        connection.starttls.assert_called_once()
        args = connection.sendmail.call_args.args
        self.assertEqual(args[1], ["receiver@example.com"])
        from email import message_from_string
        delivered_message = message_from_string(args[2])
        self.assertEqual(
            delivered_message["Subject"],
            "Morning IT Job Digest | Curated by Gemini",
        )

    def test_email_falls_back_to_ssl_after_starttls_connection_error(self):
        failing = MagicMock()
        failing.__enter__.side_effect = OSError("connection closed")
        ssl_connection = MagicMock()
        ssl_connection.__enter__.return_value = ssl_connection
        env = {
            "EMAIL_SENDER": "sender@example.com",
            "EMAIL_APP_PASSWORD": "abcdefghijklmnop",
            "EMAIL_RECEIVER": "receiver@example.com",
        }
        with patch.dict(os.environ, env, clear=True), patch(
            "main.smtplib.SMTP", return_value=failing
        ), patch("main.smtplib.SMTP_SSL", return_value=ssl_connection) as smtp_ssl:
            main.send_email_digest("<html>test</html>")

        smtp_ssl.assert_called_once()
        ssl_connection.sendmail.assert_called_once()

    def test_email_rejects_missing_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                main.send_email_digest("<html>test</html>")


class TestMainFlowAndWorkflow(unittest.TestCase):
    def test_main_runs_pipeline_in_expected_order(self):
        calls = []
        with patch.object(main, "fetch_jobs", side_effect=lambda: calls.append("fetch") or [{"job_id": "x"}]), patch.object(
            main, "evaluate_with_gemini", side_effect=lambda jobs: calls.append("evaluate") or "<html>digest</html>"
        ), patch.object(main, "publish_to_github_summary", side_effect=lambda html: calls.append("summary")), patch.object(
            main, "send_email_digest", side_effect=lambda html: calls.append("email")
        ), patch.object(main, "save_linkedin_alert_state", side_effect=lambda: calls.append("state")):
            main.main()
        self.assertEqual(calls, ["fetch", "evaluate", "summary", "email", "state"])

    def test_main_does_not_send_email_when_no_jobs_exist(self):
        with patch.object(main, "fetch_jobs", return_value=[]), patch.object(
            main, "evaluate_with_gemini"
        ) as gemini, patch.object(main, "send_email_digest") as email:
            main.main()
        gemini.assert_not_called()
        email.assert_not_called()

    def test_workflow_keeps_10am_ist_schedule_and_safe_cache_guard(self):
        workflow_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            ".github",
            "workflows",
            "daily_job_alert.yml",
        )
        with open(workflow_path, encoding="utf-8") as workflow_file:
            workflow = workflow_file.read()
        self.assertIn("cron: '37 4 * * *'", workflow)
        self.assertIn("hashFiles('.linkedin_alert_state.json') != ''", workflow)
        self.assertIn("python -m py_compile main.py", workflow)




class TestApiUsageMonitoring(unittest.TestCase):
    def test_jsearch_quota_headers_are_captured_case_insensitively(self):
        response = SimpleNamespace(
            status_code=200,
            headers={
                "X-RateLimit-Requests-Remaining": "143",
                "X-RateLimit-Requests-Limit": "200",
                "X-RateLimit-Requests-Reset": "1735689600",
            },
        )
        before = main.API_USAGE_STATS["jsearch_requests"]
        main.record_jsearch_response(response)
        self.assertEqual(main.API_USAGE_STATS["jsearch_requests"], before + 1)
        self.assertEqual(main.API_USAGE_STATS["jsearch_remaining"], "143")
        self.assertEqual(main.API_USAGE_STATS["jsearch_limit"], "200")
        self.assertEqual(main.API_USAGE_STATS["jsearch_reset"], "1735689600")

    def test_missing_quota_headers_are_not_guessed(self):
        response = SimpleNamespace(status_code=200, headers={})
        main.API_USAGE_STATS["jsearch_remaining"] = None
        main.API_USAGE_STATS["jsearch_limit"] = None
        main.record_jsearch_response(response)
        self.assertIsNone(main.API_USAGE_STATS["jsearch_remaining"])
        self.assertIsNone(main.API_USAGE_STATS["jsearch_limit"])

    def test_summary_reports_usage_and_unavailable_account_balances(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            summary_path = os.path.join(temp_dir, "summary.md")
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary_path}, clear=False):
                main.publish_api_usage_summary()
            with open(summary_path, encoding="utf-8") as summary_file:
                summary = summary_file.read()
        self.assertIn("API Usage & Quota Monitor", summary)
        self.assertIn("RapidAPI JSearch", summary)
        self.assertIn("Google Gemini", summary)
        self.assertIn("guaranteed monthly account balance", summary.lower())
        self.assertIn("SMTP does not expose", summary)


if __name__ == "__main__":
    unittest.main()

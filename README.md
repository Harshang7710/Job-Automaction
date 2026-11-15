# Job-Automaction

An automated daily job-search assistant that gathers recent IT job listings, scores them against the candidate profile with Google Gemini, and sends a curated HTML email digest.

## Job sources

- **JSearch (RapidAPI):** searches public job listings across job sites.
- **Remotive:** backup source used when JSearch returns no results.
- **LinkedIn Job Alert emails:** imports job links from LinkedIn's own alert emails delivered to a Gmail inbox. This uses Gmail IMAP and does not log in to LinkedIn or scrape LinkedIn pages.

## Daily schedule

The GitHub Actions workflow runs every day at **10:00 AM India Standard Time (IST)**. It can also be started manually from the Actions tab.

## Enable LinkedIn job alerts

1. In LinkedIn, search for each role/location you want (for example, SOC Analyst L1, QA Tester, Application Support, or CSV).
2. Turn **Job alert** on for each search.
3. Open **Manage alerts** and choose **Daily** frequency and **Email** notifications.
4. Make sure those LinkedIn alert emails arrive in a Gmail inbox.

LinkedIn supports daily or weekly job alerts by email or app notification. See [LinkedIn's job-alert instructions](https://www.linkedin.com/help/linkedin/answer/a511279/job-alerts-on-linkedin).

## GitHub Actions secrets for LinkedIn email import

Add these under **Repository → Settings → Secrets and variables → Actions → New repository secret**:

- `LINKEDIN_ALERT_EMAIL`: the Gmail inbox address that receives LinkedIn alerts.
- `LINKEDIN_ALERT_APP_PASSWORD`: a Google App Password for that Gmail inbox (not your normal Gmail password).

If `LINKEDIN_ALERT_EMAIL` is omitted, the importer uses `EMAIL_RECEIVER`. `LINKEDIN_ALERT_APP_PASSWORD` may be omitted only when that alert inbox is also the same Gmail account as `EMAIL_SENDER`, because `EMAIL_APP_PASSWORD` authenticates the sender account. Do not assume the sender's App Password can access a different receiver inbox. The importer checks messages from the previous 3 days, reads the inbox in read-only mode, and caches imported links between workflow runs to reduce duplicate entries.

**Never add your LinkedIn password, Gmail password, or app password to source code or send it in chat.** Store credentials only in GitHub Actions Secrets.

## Run tests locally

After installing dependencies, run the offline test suite with:

```bash
python -m unittest discover -s tests -v
```

The **Job Hunter QA** GitHub Actions workflow runs these tests on relevant pushes and pull requests. The tests mock external services and do not send real emails or consume job-search API quotas.

## Existing required secrets

- `GEMINI_API_KEY`
- `RAPIDAPI_KEY`
- `EMAIL_SENDER`
- `EMAIL_APP_PASSWORD`
- `EMAIL_RECEIVER`
- `EMAIL_CC` (optional; overrides the script's default CC recipient)


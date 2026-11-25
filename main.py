import os
import re
import imaplib
import json
import smtplib
from datetime import datetime, timedelta
from email import message_from_bytes
from email.header import decode_header, make_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape, unescape
from urllib.parse import urlparse
from dotenv import load_dotenv
from google import genai
import requests

load_dotenv()

API_USAGE_STATS = {
    "jsearch_requests": 0, "jsearch_success": 0, "jsearch_errors": 0,
    "jsearch_remaining": None, "jsearch_limit": None, "jsearch_reset": None,
    "remotive_requests": 0, "gemini_calls": 0, "gemini_models": [],
    "gemini_tokens": 0, "gemini_quota_errors": 0, "gemini_fallback_used": False, "email_sent": False
}


def record_jsearch_response(response):
    """Capture provider-supplied rate-limit headers without guessing the quota period."""
    stats = API_USAGE_STATS
    stats["jsearch_requests"] += 1
    if response.status_code == 200:
        stats["jsearch_success"] += 1
    else:
        stats["jsearch_errors"] += 1
    headers = {str(k).lower(): v for k, v in getattr(response, "headers", {}).items()}
    stats["jsearch_remaining"] = headers.get("x-ratelimit-requests-remaining", headers.get("x-ratelimit-remaining", stats["jsearch_remaining"]))
    stats["jsearch_limit"] = headers.get("x-ratelimit-requests-limit", headers.get("x-ratelimit-limit", stats["jsearch_limit"]))
    stats["jsearch_reset"] = headers.get("x-ratelimit-requests-reset", headers.get("x-ratelimit-reset", stats["jsearch_reset"]))


def publish_api_usage_summary():
    """Add per-run usage and any exact quota values returned by providers."""
    s = API_USAGE_STATS
    remaining = s["jsearch_remaining"] if s["jsearch_remaining"] is not None else "Not returned by API"
    limit = s["jsearch_limit"] if s["jsearch_limit"] is not None else "Not returned by API"
    reset = s["jsearch_reset"] if s["jsearch_reset"] is not None else "Not returned by API"
    lines = [
        "## API Usage & Quota Monitor", "",
        "| Service | This run | Remaining quota / details |",
        "|---|---:|---|",
        f"| RapidAPI JSearch | {s['jsearch_requests']} requests; {s['jsearch_success']} successful; {s['jsearch_errors']} errors | Remaining: {remaining}; limit: {limit}; reset: {reset} (provider headers) |",
        f"| Remotive | {s['remotive_requests']} requests | No account quota endpoint configured |",
        f"| Google Gemini | {s['gemini_calls']} model attempts; models: {', '.join(s['gemini_models']) or 'none'} | {s['gemini_tokens']} tokens reported; quota errors: {s['gemini_quota_errors']}; local fallback used: {'Yes' if s['gemini_fallback_used'] else 'No'}; account-wide remaining quota not exposed by generation response |",
        f"| Gmail SMTP | {'Sent' if s['email_sent'] else 'Not sent'} | SMTP does not expose remaining sending quota |",
        "", "Note: this is per-run telemetry, not a guaranteed monthly account balance.", ""
    ]
    print("\n".join(lines))
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n")


USER_PROFILE = """
Candidate: Harshang Patel (patidar4774@gmail.com)
Education: Bachelor of Engineering (Information Technology)
Total Experience: Entry-level to ~2+ years (Junior to Associate level)

Background & Experience:
- QA IT Executive at Alembic Pharmaceuticals (System Validation IQ/OQ/PQ, GAMP 5, 21 CFR Part 11 compliance, manual functional, regression, smoke, and UAT testing)
- Web Application & Portal QA testing (responsive testing, defect tracking, JIRA)
- Hands-on projects in Python, Flask, HTML/CSS, basic security tools

Core & Emerging Skills:
- QA & Testing: Manual Testing, Functional/Regression/UAT, Test Case Design, Defect Lifecycle, JIRA, Postman (API Testing), Selenium WebDriver
- IT, Systems & Networking: TCP/IP, OSI model, Subnetting, DNS/DHCP, Linux/Windows Server basics, Network Security (Firewalls, VPN)
- Security Tools: Wireshark, Burp Suite (Foundational)

Target Roles (evaluate all five categories fairly):
- Cybersecurity: SOC Analyst L1 / Tier 1, Junior Security Analyst, Security Operations Analyst, Junior GRC, Information Security Compliance, IT Risk, Security Audit Associate.
- QA & Testing: Manual Tester, QA Engineer, QA Test Engineer, Software Test Engineer, Junior SDET, UAT / Functional / Regression Tester.
- IT Support: Application Support Engineer, Technical Support Engineer, IT Support Engineer, Helpdesk / Service Desk Support.
- Validation & Compliance: Computer System Validation (CSV), CSV Analyst, IT Compliance, GxP / GAMP 5 validation, Pharma / Healthcare IT Validation.
- Networking: Network Support Engineer, Junior Network Engineer, Network Operations Support, Junior Network Administrator.

Target Locations: Search across India and consider global / worldwide remote opportunities only when the listing explicitly confirms worldwide or India eligibility. Do not assume a generic "Remote" label means India applicants are eligible.
Experience fit: Prefer 0-2 years, trainee, graduate, associate, junior, and L1 roles. Consider up to 3 years only when the role is clearly junior and requirements fit the profile. Exclude senior, lead, manager, architect, and other advanced roles, as well as roles requiring 4+ years, non-technical BPO, or pure sales.
Do not claim certifications, tools, hands-on security experience, or qualifications not listed above.
"""

SEARCH_QUERIES = [
    "SOC Analyst L1 Junior Security Analyst GRC India 0-2 years",
    "Manual Tester QA Engineer Junior SDET India 0-2 years",
    "Application Support Technical Support IT Support India entry level",
    "Computer System Validation CSV IT Compliance GxP India junior",
    "Network Support Junior Network Engineer India 0-2 years",
    "remote worldwide SOC Analyst L1 Junior GRC",
    "remote worldwide Manual Tester QA Engineer Junior SDET",
    "remote worldwide Application Support Technical Support",
    "remote worldwide Computer System Validation CSV IT Compliance",
    "remote worldwide Network Support Junior Network Engineer"
]

def extract_job_list(payload):
    """Extract job dictionaries safely from JSearch response variants."""
    import json

    job_markers = {
        "job_id", "job_title", "employer_name", "job_apply_link",
        "job_description", "job_publisher"
    }
    jobs = []
    visited = set()

    def walk(value):
        if isinstance(value, str):
            candidate = value.strip()
            if candidate.startswith(("{", "[")):
                try:
                    walk(json.loads(candidate))
                except (json.JSONDecodeError, RecursionError):
                    pass
            return

        if isinstance(value, list):
            for item in value:
                walk(item)
            return

        if not isinstance(value, dict):
            return

        if job_markers.intersection(value.keys()):
            marker = value.get("job_id") or value.get("job_apply_link") or repr(sorted(value.items())[:4])
            marker = str(marker)
            if marker not in visited:
                visited.add(marker)
                jobs.append(value)
            return

        preferred = ("data", "jobs", "results", "items", "search_results")
        for key in preferred:
            if key in value:
                walk(value[key])
        for key, nested in value.items():
            if key not in preferred and isinstance(nested, (dict, list, str)):
                walk(nested)

    walk(payload)
    return jobs


LINKEDIN_PENDING_LINKS = set()


def _decode_email_header(value):
    """Decode an RFC 2047 email header safely."""
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""

def _get_email_bodies(message):
    """Extract decoded plain-text and HTML bodies from an email."""
    plain_parts, html_parts = [], []
    for part in message.walk():
        if part.get_content_maintype() == "multipart" or part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            decoded = payload.decode(charset, errors="replace")
        except LookupError:
            decoded = payload.decode("utf-8", errors="replace")
        (html_parts if content_type == "text/html" else plain_parts).append(decoded)
    return "\n".join(plain_parts), "\n".join(html_parts)

def fetch_linkedin_alert_jobs():
    """
    Import job links from LinkedIn's own job-alert emails using Gmail IMAP.
    This does not log in to LinkedIn or scrape LinkedIn pages.
    """
    alert_email = (os.getenv("LINKEDIN_ALERT_EMAIL") or os.getenv("EMAIL_RECEIVER") or "").strip()
    app_password = "".join((os.getenv("LINKEDIN_ALERT_APP_PASSWORD") or "").split())
    smtp_sender = (os.getenv("EMAIL_SENDER") or "").strip()
    if not app_password and alert_email and smtp_sender.lower() == alert_email.lower():
        app_password = "".join((os.getenv("EMAIL_APP_PASSWORD") or "").split())

    if not alert_email or not app_password:
        print(
            "LinkedIn alert import skipped: configure LINKEDIN_ALERT_EMAIL and "
            "LINKEDIN_ALERT_APP_PASSWORD. EMAIL_APP_PASSWORD can only be reused "
            "when the alert inbox is also EMAIL_SENDER."
        )
        return []

    state_path = ".linkedin_alert_state.json"
    persisted_links = set()
    try:
        with open(state_path, "r", encoding="utf-8") as state_file:
            persisted_links = set(json.load(state_file).get("seen_links", []))
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"LinkedIn alert state could not be read: {exc}")

    jobs, seen_links = [], set(persisted_links)
    since_date = (datetime.now() - timedelta(days=3)).strftime("%d-%b-%Y")
    try:
        mailbox = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)
        try:
            mailbox.login(alert_email, app_password)
            status, _ = mailbox.select("INBOX", readonly=True)
            if status != "OK":
                print("LinkedIn alert import skipped: could not open Gmail INBOX.")
                return []

            status, data = mailbox.search(None, "SINCE", since_date)
            if status != "OK" or not data or not data[0]:
                print("LinkedIn alert import: no recent messages found.")
                return []

            # Inspect only the latest 100 messages from the previous 3 days.
            anchor_re = re.compile(
                r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a\s*>',
                re.IGNORECASE | re.DOTALL
            )
            for message_id in data[0].split()[-100:]:
                status, parts = mailbox.fetch(message_id, "(RFC822)")
                raw = next((item[1] for item in parts if isinstance(item, tuple)), None) if status == "OK" else None
                if not raw:
                    continue

                msg = message_from_bytes(raw)
                sender = _decode_email_header(msg.get("From", "")).lower()
                subject = _decode_email_header(msg.get("Subject", "")).strip()
                plain, html = _get_email_bodies(msg)
                plain_clean = re.sub(r"\s+", " ", unescape(plain or re.sub(r"<[^>]+>", " ", html))).strip()

                if "linkedin" not in sender and "linkedin" not in subject.lower():
                    continue
                if "job" not in subject.lower() and "job" not in plain_clean.lower():
                    continue

                for match in anchor_re.finditer(html):
                    link = unescape(match.group(1)).replace("&amp;", "&").strip()
                    parsed = urlparse(link)
                    host, path = (parsed.netloc or "").lower(), (parsed.path or "").lower()
                    linkedin_host = host == "linkedin.com" or host.endswith(".linkedin.com")
                    job_url = "/jobs/view/" in path or "/comm/jobs/view/" in path or "currentjobid=" in link.lower()
                    if not linkedin_host or not job_url or link in seen_links:
                        continue

                    title = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", match.group(2)))).strip()
                    if not title or title.lower() in {"view job", "view", "apply", "see job", "view jobs"}:
                        title = subject or "LinkedIn job alert"

                    lo, hi = max(0, match.start() - 900), min(len(html), match.end() + 1400)
                    context = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html[lo:hi]))).strip()
                    if not context:
                        context = plain_clean[:1800]

                    seen_links.add(link)
                    LINKEDIN_PENDING_LINKS.add(link)
                    jobs.append({
                        "job_id": "linkedin-alert:" + link,
                        "job_title": title,
                        "employer_name": "",
                        "job_city": "",
                        "job_country": "",
                        "job_is_remote": None,
                        "job_description": context[:2500],
                        "job_publisher": "LinkedIn Job Alert Email",
                        "job_apply_link": link,
                        "job_apply_is_direct": False
                    })
        finally:
            try:
                mailbox.logout()
            except Exception:
                pass
    except Exception as exc:
        print(f"LinkedIn alert import warning: {type(exc).__name__}: {exc}")

    print(f"Imported {len(jobs)} new job links from LinkedIn alert emails.")
    return jobs

def save_linkedin_alert_state():
    """Persist imported links only after the daily digest email was sent."""
    if not LINKEDIN_PENDING_LINKS:
        return
    state_path = ".linkedin_alert_state.json"
    existing = set()
    try:
        with open(state_path, "r", encoding="utf-8") as state_file:
            existing = set(json.load(state_file).get("seen_links", []))
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"LinkedIn alert state could not be loaded before saving: {exc}")

    existing.update(LINKEDIN_PENDING_LINKS)
    # Keep the state file compact while retaining enough history for deduplication.
    with open(state_path, "w", encoding="utf-8") as state_file:
        json.dump({"seen_links": sorted(existing)[-2000:]}, state_file, indent=2)
    print(f"Saved {len(LINKEDIN_PENDING_LINKS)} LinkedIn links for duplicate prevention.")

def fetch_jobs():
    rapidapi_key = "".join((os.getenv("RAPIDAPI_KEY") or "").split())
    all_jobs = []
    seen_ids = set()

    if rapidapi_key:
        url = "https://jsearch.p.rapidapi.com/search-v2"
        headers = {
            "X-RapidAPI-Key": rapidapi_key,
            "X-RapidAPI-Host": "jsearch.p.rapidapi.com"
        }

        for q in SEARCH_QUERIES:
            params = {"query": q, "page": "1", "num_pages": "1", "date_posted": "3days"}
            try:
                response = requests.get(url, headers=headers, params=params, timeout=20)
                record_jsearch_response(response)
                print(f"JSearch Query '{q}' -> HTTP Status: {response.status_code}")
                if response.status_code == 200:
                    jobs_data = extract_job_list(response.json())
                    print(f"  Extracted {len(jobs_data)} valid jobs for '{q}'")
                    for job in jobs_data:
                        job_id = str(job.get("job_id") or job.get("id") or job.get("job_apply_link") or "")
                        if job_id and job_id not in seen_ids:
                            seen_ids.add(job_id)
                            all_jobs.append(job)
                else:
                    print(f"  API Warning: {response.text[:250]}")
                    # Stop spending calls once RapidAPI explicitly reports monthly exhaustion.
                    if response.status_code == 429 and "monthly quota" in response.text.lower():
                        print("JSearch monthly quota is exhausted; stopping further JSearch queries.")
                        break
            except Exception as e:
                API_USAGE_STATS["jsearch_errors"] += 1
                print(f"Error fetching query '{q}': {e}")
    else:
        print("Warning: RAPIDAPI_KEY secret is empty.")

    # Use the free backup whenever JSearch returns fewer than five usable jobs.
    # This also prevents a partially exhausted API quota from producing a thin digest.
    if len(all_jobs) < 5:
        print(f"Fetching from free backup job board (Remotive API); JSearch supplied only {len(all_jobs)} jobs...")
        for bq in ["SOC analyst", "security operations", "GRC", "vulnerability analyst", "penetration testing", "red team", "QA", "IT support", "validation", "network support"]:
            try:
                resp = requests.get(f"https://remotive.com/api/remote-jobs?search={bq}&limit=10", timeout=15)
                API_USAGE_STATS["remotive_requests"] += 1
                if resp.status_code == 200:
                    for item in resp.json().get("jobs", []):
                        jid = str(item.get("id"))
                        if jid not in seen_ids:
                            seen_ids.add(jid)
                            all_jobs.append({
                                "job_title": item.get("title"),
                                "employer_name": item.get("company_name"),
                                "job_city": item.get("candidate_required_location", "Remote"),
                                "job_country": "Global / Remote",
                                "job_is_remote": True,
                                "job_description": item.get("description", ""),
                                "job_apply_link": item.get("url"),
                                "job_publisher": "Remotive"
                            })
            except Exception as e:
                print(f"Backup fetch error for '{bq}': {e}")

    # Also import job links from LinkedIn's own email alerts.
    for job in fetch_linkedin_alert_jobs():
        job_id = str(job.get("job_id") or job.get("job_apply_link") or "")
        if job_id and job_id not in seen_ids:
            seen_ids.add(job_id)
            all_jobs.append(job)

    return all_jobs

def generate_with_fallback(client, prompt):
    """Try current Gemini Flash models; report failures for the local fallback."""
    candidate_models = ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.6-flash"]
    for model_name in candidate_models:
        try:
            print(f"Trying Gemini model: {model_name}...")
            API_USAGE_STATS["gemini_calls"] += 1
            API_USAGE_STATS["gemini_models"].append(model_name)
            response = client.models.generate_content(model=model_name, contents=prompt)
            usage = getattr(response, "usage_metadata", None)
            if usage is not None:
                API_USAGE_STATS["gemini_tokens"] += int(getattr(usage, "total_token_count", 0) or 0)
            if not getattr(response, "text", None) or not response.text.strip():
                raise RuntimeError(f"{model_name} returned an empty response.")
            print(f"Gemini succeeded with {model_name}.")
            return response
        except Exception as error:
            message = str(error)
            upper = message.upper()
            if "429" in message or "RESOURCE_EXHAUSTED" in upper or "QUOTA" in upper:
                API_USAGE_STATS["gemini_quota_errors"] += 1
                print(f"Gemini quota/rate limit: {message[:300]}")
                if "RESOURCE_EXHAUSTED" in upper or "QUOTA" in upper:
                    break
            else:
                print(f"Gemini model {model_name} failed: {message[:300]}")
    raise RuntimeError("All configured Gemini models failed. Check API key, model access, quota, and workflow logs.")


def select_jobs_for_evaluation(raw_jobs, max_jobs=50, linkedin_limit=15):
    """Keep a fair sample and reserve slots for jobs from LinkedIn alerts."""
    if max_jobs <= 0:
        return []

    linkedin_jobs = [
        job for job in raw_jobs
        if job.get("job_publisher") == "LinkedIn Job Alert Email"
    ]
    other_jobs = [
        job for job in raw_jobs
        if job.get("job_publisher") != "LinkedIn Job Alert Email"
    ]

    linkedin_slots = min(len(linkedin_jobs), max(0, min(linkedin_limit, max_jobs)))
    selected = linkedin_jobs[:linkedin_slots]
    selected.extend(other_jobs[:max_jobs - len(selected)])

    if len(selected) < max_jobs:
        selected.extend(linkedin_jobs[linkedin_slots:max_jobs - len(selected) + linkedin_slots])

    return selected[:max_jobs]


def evaluate_without_gemini(raw_jobs):
    """Create a conservative local digest when Gemini is unavailable."""
    scored = []
    role_groups = {
        "Cybersecurity": ["soc analyst", "security operations", "security analyst", "cybersecurity", "cyber security", "grc", "information security", "it risk"],
        "QA & Testing": ["manual tester", "manual testing", "qa engineer", "quality assurance", "software test", "sdet", "uat tester"],
        "IT Support": ["application support", "it support", "technical support", "helpdesk", "help desk", "service desk"],
        "Validation & Compliance": ["computer system validation", "csv", "gamp", "21 cfr", "it compliance", "gxp", "pharma validation"],
        "Networking": ["network support", "network engineer", "network operations", "network administrator"]
    }
    senior_terms = ("senior", "sr.", "lead", "manager", "architect", "director", "principal", "head of")
    for job in select_jobs_for_evaluation(raw_jobs):
        title = str(job.get("job_title") or job.get("title") or "").strip()
        company = str(job.get("employer_name") or job.get("company_name") or "Company not specified").strip()
        description = str(job.get("job_description") or job.get("description") or "")
        text = (title + " " + description).lower()
        if not title or any(term in title.lower() for term in senior_terms):
            continue
        if re.search(r"\b(?:4|5|6|7|8|9|10)\s*\+?\s*years?\b", text):
            continue
        title_text = title.lower()
        matched = [
            group for group, terms in role_groups.items()
            if any(term in title_text for term in terms)
        ]
        if not matched:
            continue
        score = 48 + min(24, sum(4 for _ in matched))
        if any(term in title.lower() for term in ("junior", "trainee", "graduate", "entry", "associate", "l1", "tier 1")):
            score += 10
        if re.search(r"\b(?:0|1|2)\s*(?:-|to)\s*(?:1|2|3)?\s*years?\b|\b(?:0|1|2)\s*\+?\s*years?\b", text):
            score += 8
        if any(term in text for term in ("4+ years", "5+ years", "6+ years")):
            continue
        score = min(score, 90)
        reason = "Relevant to the candidate profile in " + ", ".join(matched) + ". Requirements still need manual verification."
        location = ", ".join(str(job.get(k) or "").strip() for k in ("job_city", "job_country") if job.get(k))
        if not location:
            location = str(job.get("location") or ("Remote / Global" if job.get("job_is_remote") else "Location not specified"))
        apply_url = job.get("job_apply_link") or job.get("apply_link") or job.get("url") or ""
        if not str(apply_url).lower().startswith(("https://", "http://")):
            apply_url = ""
        source = job.get("job_publisher") or job.get("publisher") or job.get("source") or "Source not supplied"
        career = job.get("employer_website") or job.get("company_website") or job.get("career_page_url") or ""
        hr_email = job.get("hr_email") or job.get("recruiter_email") or job.get("contact_email") or ""
        hr_phone = job.get("hr_phone") or job.get("recruiter_phone") or job.get("contact_phone") or ""
        scored.append((score, title, company, location, reason, apply_url, source, career, hr_email, hr_phone, bool(job.get("job_is_remote")), bool(job.get("job_apply_is_direct"))))
    scored.sort(key=lambda item: item[0], reverse=True)
    selected = scored[:10]
    if not selected:
        return """<!doctype html><html><body style="font-family:Arial,sans-serif;background:#f1f5f9;padding:24px;color:#0f172a">
        <div style="max-width:720px;margin:auto;background:#fff;padding:24px;border-radius:12px">
        <h2 style="background:#0f172a;color:#fff;padding:18px;border-radius:8px">Morning IT Job Digest</h2>
        <p>Today's sources returned postings, but none could be confidently matched to the current entry-level QA, validation, IT support, networking, or cybersecurity profile.</p>
        <p>Please review the original job sources again later. No unsuitable roles have been added just to fill the digest.</p></div></body></html>"""
    cards = []
    for score, title, company, location, reason, apply_url, source, career, hr_email, hr_phone, remote, is_direct in selected:
        safe_title, safe_company = escape(title), escape(company)
        safe_location, safe_reason, safe_source = escape(location), escape(reason), escape(str(source))
        link_label = "Direct Apply" if is_direct else "View Job / Apply"
        link = f'<a href="{escape(str(apply_url), quote=True)}" style="display:inline-block;background:#2563eb;color:#fff;padding:10px 16px;border-radius:7px;text-decoration:none">{link_label}</a>' if apply_url else '<span>Application link not supplied</span>'
        extra = []
        if career and str(career).startswith(("https://", "http://")):
            extra.append(f'<div>Company careers: <a href="{escape(str(career), quote=True)}">Visit careers page</a></div>')
        if hr_email:
            extra.append(f'<div>Public HR email: {escape(str(hr_email))}</div>')
        if hr_phone:
            extra.append(f'<div>Public HR phone: {escape(str(hr_phone))}</div>')
        extras_html = "".join(extra) if extra else "<div>Public HR contact not available</div>"
        remote_label = " · Remote / Global" if remote else ""
        cards.append(f"""<article style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:18px;margin:14px 0">
        <div style="font-size:12px;color:#64748b">MATCH SCORE: {score}/100</div>
        <h3 style="margin:8px 0;color:#0f172a">{safe_title}</h3><div style="font-weight:600">{safe_company}</div>
        <p style="color:#475569">{safe_location}{remote_label}</p><p>{safe_reason}</p>
        <div>{link}</div><p style="font-size:12px;color:#64748b">Source: {safe_source}</p>
        <div style="font-size:13px;color:#475569">{extras_html}</div></article>""")
    return """<!doctype html><html><body style="margin:0;background:#f1f5f9;font-family:Arial,sans-serif;color:#0f172a;padding:20px">
    <main style="max-width:760px;margin:auto"><header style="background:#0f172a;color:#fff;padding:22px;border-radius:12px">
    <h1 style="margin:0;font-size:24px">Morning IT Job Digest</h1>
    <p style="margin:8px 0 0;color:#cbd5e1">Fallback screening · Gemini temporarily unavailable</p></header>
    <p style="color:#475569">These are preliminary keyword-based matches, not AI-verified recommendations. Check each job's full requirements before applying.</p>""" + "".join(cards) + """<footer style="color:#64748b;font-size:12px;padding:16px">Curated for the candidate profile. No unverified qualifications or contact details have been added.</footer></main></body></html>"""


def evaluate_with_gemini(raw_jobs):
    compact_jobs = []
    for job in select_jobs_for_evaluation(raw_jobs):
        compact_jobs.append({
            "title": job.get("job_title") or job.get("title"),
            "company": job.get("employer_name") or job.get("company_name"),
            "location": f"{job.get('job_city') or job.get('location') or ''}, {job.get('job_country') or ''}",
            "is_remote": job.get("job_is_remote"),
            "description": (job.get("job_description") or job.get("description") or "")[:900],
            "source_platform": job.get("job_publisher") or job.get("publisher") or job.get("source") or "JSearch listing (publisher not supplied)",
            "apply_link": job.get("job_apply_link") or job.get("apply_link") or job.get("url"),
            "apply_link_is_direct": job.get("job_apply_is_direct"),
            "company_career_page": job.get("employer_website") or job.get("company_website") or job.get("career_page_url"),
            "public_hr_email": job.get("hr_email") or job.get("recruiter_email") or job.get("contact_email"),
            "public_hr_phone": job.get("hr_phone") or job.get("recruiter_phone") or job.get("contact_phone")
        })

    prompt = f"""
    You are Harshang Patel's dedicated AI Career Agent.

    Candidate background and preferences:
    {USER_PROFILE}

    Job postings retrieved today:
    {compact_jobs}

    Screening and ranking rules:
    1. Evaluate all FIVE requested categories fairly: Cybersecurity (SOC L1, Junior Security Analyst, GRC); QA & Testing (Manual Tester, QA Engineer, Junior SDET); IT Support (Application Support, Technical Support); Validation (CSV, IT Compliance); Networking (Network Support, Junior Network Engineer). Do not give cybersecurity automatic priority.
    2. Prefer 0-2 years, trainee, graduate, junior, associate, and L1 roles. Consider up to 3 years only for clearly junior roles. Exclude roles requiring 4+ years or senior, lead, manager, architect responsibilities.
    3. Search India-wide. Include global remote jobs only if the listing explicitly confirms worldwide or India eligibility. Generic "Remote" or US/EU-only does not prove eligibility for an India-based applicant.
    4. Check actual duties and requirements; never invent experience, qualifications, tools, or certifications.
    5. Return only genuinely suitable roles; do not pad the list with unrelated jobs.
    6. Format the output as a clean, responsive HTML email digest using inline CSS and a modern dark navy/slate theme (#0f172a headers, #f8fafc card backgrounds, #2563eb buttons).
       For each job card include job title and company, location and work mode (highlight Remote/Global), a match score and one-sentence explanation grounded in the posting/profile, and a clickable application link. Label it "Direct Apply" only when apply_link_is_direct is true; otherwise say "View Job / Apply". Include source publisher only when supplied, a separate company-careers link only when a URL is supplied, and public HR contact only when supplied (otherwise say "Public HR contact not available").
    Use only URLs, emails, phone numbers, and publisher names explicitly present in the supplied job data. Never invent or guess career URLs, contacts, recruiter identities, or phone numbers. Do not present a job-board redirect as a direct company application link. Keep existing styling and candidate targeting.

    CRITICAL: Output ONLY the raw HTML string. Do NOT wrap it in markdown code blocks or triple backticks.
    """

    try:
        gemini_key = "".join((os.getenv("GEMINI_API_KEY") or "").split())
        if not gemini_key:
            raise ValueError("GEMINI_API_KEY secret is missing in GitHub Secrets.")
        client = genai.Client(api_key=gemini_key)
        response = generate_with_fallback(client, prompt)
        html_output = (response.text or "").strip()
        if html_output.startswith("```html"):
            html_output = html_output[7:]
        if html_output.endswith("```"):
            html_output = html_output[:-3]
        if html_output.strip():
            return html_output.strip()
        raise RuntimeError("Gemini returned an empty digest.")
    except Exception as exc:
        API_USAGE_STATS["gemini_fallback_used"] = True
        print(f"Gemini screening unavailable; using conservative local screening instead: {type(exc).__name__}: {exc}")
        return evaluate_without_gemini(raw_jobs)

def publish_to_github_summary(html_content):
    """Writes the HTML job digest to the GitHub Actions run summary."""
    summary_file = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_file:
        try:
            with open(summary_file, "a", encoding="utf-8") as f:
                f.write("## Daily Curated IT Job Digest for Harshang Patel\n\n")
                f.write(html_content + "\n")
            print("Published job digest directly to GitHub Actions Summary page!")
        except Exception as e:
            print(f"Could not write GitHub summary: {e}")

def send_email_digest(html_content):
    """Send the digest through Gmail, trying STARTTLS then implicit SSL."""
    sender = "".join((os.getenv("EMAIL_SENDER") or "").split())
    password = "".join((os.getenv("EMAIL_APP_PASSWORD") or "").split())
    receiver = "".join((os.getenv("EMAIL_RECEIVER") or "").split())
    cc = "".join((os.getenv("EMAIL_CC") or "harshang.patel@alembic.co.in").split())

    if not all([sender, password, receiver]):
        raise ValueError("Missing EMAIL_SENDER, EMAIL_APP_PASSWORD, or EMAIL_RECEIVER in GitHub Secrets.")
    if len(password) != 16:
        raise ValueError("EMAIL_APP_PASSWORD should be the 16-character Google App Password (remove spaces; do not use your normal Gmail password).")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Morning IT Job Digest | Curated by Gemini"
    msg["From"] = sender
    msg["To"] = receiver
    if cc:
        msg["Cc"] = cc
    msg.attach(MIMEText(html_content, "html", "utf-8"))
    recipients = [receiver] + ([cc] if cc and cc.lower() != receiver.lower() else [])

    failures = []
    try:
        print("Attempting Gmail SMTP on port 587 (STARTTLS)...")
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(sender, password)
            server.sendmail(sender, recipients, msg.as_string())
        API_USAGE_STATS["email_sent"] = True
        print("SUCCESS: Digest email sent using Gmail port 587.")
        return
    except (OSError, smtplib.SMTPException) as exc:
        failures.append(f"Port 587 STARTTLS: {type(exc).__name__}: {exc}")
        print(f"Port 587 attempt failed: {type(exc).__name__}: {exc}")

    try:
        print("Attempting Gmail SMTP on port 465 (SSL)...")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.ehlo()
            server.login(sender, password)
            server.sendmail(sender, recipients, msg.as_string())
        API_USAGE_STATS["email_sent"] = True
        print("SUCCESS: Digest email sent using Gmail port 465.")
        return
    except (OSError, smtplib.SMTPException) as exc:
        failures.append(f"Port 465 SSL: {type(exc).__name__}: {exc}")

    raise RuntimeError("Gmail could not send the digest using either SMTP connection. Check EMAIL_SENDER, 2-Step Verification, and the current 16-character Google App Password. Do not use the normal Gmail password. " + " | ".join(failures))

def main():
    print("Step 1: Searching recent cybersecurity, IT, QA, support, and validation jobs...")
    jobs = fetch_jobs()
    print(f"Found {len(jobs)} unique raw postings.")

    if not jobs:
        print("No job postings found from primary or backup sources.")
        publish_api_usage_summary()
        return

    print("Step 2: Scoring and filtering postings with Gemini...")
    digest_html = evaluate_with_gemini(jobs)
    publish_to_github_summary(digest_html)

    print("Step 3: Delivering your morning email alert...")
    send_email_digest(digest_html)
    save_linkedin_alert_state()
    publish_api_usage_summary()

if __name__ == "__main__":
    main()

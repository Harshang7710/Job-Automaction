import os
import re
import imaplib
import smtplib
from datetime import datetime, timedelta
from email import message_from_bytes
from email.header import decode_header, make_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import unescape
from urllib.parse import urlparse
from dotenv import load_dotenv
from google import genai
import requests

load_dotenv()

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

Target Roles (consider all relevant categories):
- Cybersecurity: SOC Analyst L1 / Tier 1, Security Operations Analyst, Junior Security Analyst, Junior GRC / Information Security Compliance / IT Risk / Security Audit Associate, Entry-level Vulnerability Management / Assessment Analyst, junior network security; red team / penetration testing only for genuine trainee, intern, graduate, or junior roles that do not require substantial prior offensive-security experience
- QA & Testing: QA Test Engineer, Manual Tester, Software Test Engineer, Junior SDET, UAT / functional / regression tester
- Validation & Compliance: QA IT Executive, Computer System Validation (CSV), IT Compliance, pharma / healthcare validation
- IT & Support: Application Support, IT Support, Technical Support Engineer, Network Support Engineer

Target Locations: Pan-India (Vadodara, Ahmedabad, Pune, Bengaluru, Mumbai, Hyderabad, Remote) AND Global / Worldwide opportunities.
Experience fit: Prefer 0-2 years, trainee, graduate, associate, junior, and L1 roles. Consider up to 3 years only when the role is clearly junior and requirements fit the profile. Exclude senior, lead, manager, architect, and other advanced roles, as well as roles requiring 4+ years of experience, non-technical BPO, or pure sales.
Do not claim certifications, tools, hands-on security experience, or qualifications not listed above.
"""

SEARCH_QUERIES = [
    "entry level SOC Analyst L1 Tier 1 India",
    "junior cybersecurity analyst security operations India 0-2 years",
    "graduate trainee security operations analyst India",
    "junior GRC information security compliance risk analyst India",
    "entry level information security audit associate India",
    "junior vulnerability management analyst vulnerability assessment India",
    "trainee junior penetration tester red team India entry level",
    "entry level network security analyst India",
    "QA Tester in India",
    "Manual QA Tester junior India",
    "Application Support Engineer entry level India",
    "IT Support Technical Support Engineer junior India",
    "Computer System Validation QA CSV India",
    "Pharma IT compliance validation associate India",
    "Network Support Engineer junior India",
    "Junior Network Engineer India"
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
    app_password = "".join((
        os.getenv("LINKEDIN_ALERT_APP_PASSWORD") or os.getenv("EMAIL_APP_PASSWORD") or ""
    ).split())
    if not alert_email or not app_password:
        print("LinkedIn alert import skipped: Gmail inbox credentials are not configured.")
        return []

    jobs, seen_links = [], set()
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

    print(f"Imported {len(jobs)} job links from LinkedIn alert emails.")
    return jobs

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
            except Exception as e:
                print(f"Error fetching query '{q}': {e}")
    else:
        print("Warning: RAPIDAPI_KEY secret is empty.")

    # Free backup source if JSearch returns no jobs.
    if not all_jobs:
        print("Fetching from free backup job board (Remotive API)...")
        for bq in ["SOC analyst", "security operations", "GRC", "vulnerability analyst", "penetration testing", "red team", "QA", "IT support", "validation", "network support"]:
            try:
                resp = requests.get(f"https://remotive.com/api/remote-jobs?search={bq}&limit=10", timeout=15)
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
                                "job_apply_link": item.get("url")
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
    candidate_models = [
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
        "gemini-2.0-flash"
    ]
    for model_name in candidate_models:
        try:
            print(f"Trying Gemini model: {model_name}...")
            return client.models.generate_content(model=model_name, contents=prompt)
        except Exception as e:
            print(f"  Model {model_name} unavailable ({e}). Trying next...")

    raise RuntimeError("Could not find an active Gemini model for this API key.")

def evaluate_with_gemini(raw_jobs):
    gemini_key = "".join((os.getenv("GEMINI_API_KEY") or "").split())
    if not gemini_key:
        raise ValueError("GEMINI_API_KEY secret is missing in GitHub Secrets!")

    client = genai.Client(api_key=gemini_key)
    compact_jobs = []
    for job in raw_jobs[:50]:
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
    1. Search and recommend jobs across ALL suitable categories: cybersecurity, IT, QA/testing, application/technical support, network support, and validation/compliance (especially CSV and pharma/healthcare IT). Cybersecurity is important, but it is NOT the only target and must not automatically outrank a better-fitting IT, QA, support, or validation role. Evaluate the full range and rank by overall fit and realistic entry-level suitability.
    2. Prefer trainee, graduate, junior, associate, and L1/Tier 1 roles, typically requiring 0-2 years. A clearly junior role asking up to 3 years may be considered if duties and requirements fit the candidate. Exclude roles requiring 4+ years or describing senior, lead, manager, architect, or advanced responsibilities.
    3. Include red-team/penetration-testing roles only if genuinely entry-level, trainee, intern, graduate, or junior; exclude jobs requiring substantial professional offensive-security experience or skills/certifications absent from the profile.
    4. Review the actual requirements and responsibilities, not just titles or keywords. Be honest about experience or qualification gaps; do not claim certifications, tools, or hands-on experience not listed in the candidate profile.
    5. Select the best 5 to 10 suitable jobs across the categories. If fewer than 5 genuinely suitable jobs exist, show only those suitable results rather than padding with poor matches. Aim for a useful mix of relevant categories when the postings support it.
    6. Format the output as a clean, responsive HTML email digest using inline CSS and a modern dark navy/slate theme (#0f172a headers, #f8fafc card backgrounds, #2563eb buttons).
       For each job card include job title and company, location and work mode (highlight Remote/Global), a match score and one-sentence explanation grounded in the posting/profile, and a clickable application link. Label it "Direct Apply" only when apply_link_is_direct is true; otherwise say "View Job / Apply". Include source publisher only when supplied, a separate company-careers link only when a URL is supplied, and public HR contact only when supplied (otherwise say "Public HR contact not available").
    Use only URLs, emails, phone numbers, and publisher names explicitly present in the supplied job data. Never invent or guess career URLs, contacts, recruiter identities, or phone numbers. Do not present a job-board redirect as a direct company application link. Keep existing styling and candidate targeting.

    CRITICAL: Output ONLY the raw HTML string. Do NOT wrap it in markdown code blocks or triple backticks.
    """

    response = generate_with_fallback(client, prompt)
    html_output = response.text.strip()
    if html_output.startswith("```html"):
        html_output = html_output[7:]
    if html_output.endswith("```"):
        html_output = html_output[:-3]
    return html_output.strip()

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
    cc = "".join((os.getenv("EMAIL_CC") or "harshangpatel@alembic.co.in").split())

    if not all([sender, password, receiver]):
        raise ValueError("Missing EMAIL_SENDER, EMAIL_APP_PASSWORD, or EMAIL_RECEIVER in GitHub Secrets.")
    if len(password) != 16:
        raise ValueError("EMAIL_APP_PASSWORD should be the 16-character Google App Password (remove spaces; do not use your normal Gmail password).")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Morning Job Digest | Curated by Gemini"
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
        return

    print("Step 2: Scoring and filtering postings with Gemini...")
    digest_html = evaluate_with_gemini(jobs)
    publish_to_github_summary(digest_html)

    print("Step 3: Delivering your morning email alert...")
    send_email_digest(digest_html)

if __name__ == "__main__":
    main()

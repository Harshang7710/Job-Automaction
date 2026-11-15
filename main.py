import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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

Cybersecurity roles are the primary career goal. Target entry-level roles:
- SOC Analyst L1 / Tier 1, Security Operations Analyst, Junior Security Analyst, security monitoring and incident triage
- Junior GRC / Information Security Compliance / IT Risk Analyst / Security Audit Associate
- Entry-level Vulnerability Management Analyst / Vulnerability Assessment Associate
- Security Operations or junior network-security roles
- Red Team / penetration testing only for genuine trainee, intern, graduate, or junior openings that do not require substantial prior offensive-security experience

Other relevant roles (secondary priority):
- QA Test Engineer / Manual Tester / Software Test Engineer / SDET (Junior)
- QA IT Executive / CSV (Computer System Validation) / IT Compliance in Pharma or Healthcare
- Application Support / IT Support / Technical Support Engineer / Network Support Engineer

Target Locations: Pan-India (Vadodara, Ahmedabad, Pune, Bengaluru, Mumbai, Hyderabad, Remote) AND Global / Worldwide opportunities.
Experience fit: Prefer 0-2 years, trainee, graduate, associate, junior, and L1 roles. Consider up to 3 years only when the role is clearly junior and the requirements fit the profile. Exclude senior, lead, manager, architect, and other advanced roles, as well as roles requiring 4+ years of experience, non-technical BPO, or pure sales.
Do not claim certifications, tools, hands-on security experience, or qualifications not listed above.
"""

# Cybersecurity searches come first so they are represented in the digest when results are capped.
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
    "Application Support Engineer in India",
    "Computer System Validation QA in India",
    "Network Engineer in India"
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
            # Some wrappers return JSON-encoded objects as strings.
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

        # Search common response containers first, then other nested values.
        preferred = ("data", "jobs", "results", "items", "search_results")
        for key in preferred:
            if key in value:
                walk(value[key])
        for key, nested in value.items():
            if key not in preferred and isinstance(nested, (dict, list, str)):
                walk(nested)

    walk(payload)
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
            params = {
                "query": q,
                "page": "1",
                "num_pages": "1",
                "date_posted": "3days"
            }
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

    # Free Backup Source if JSearch returns 0 jobs
    if not all_jobs:
        print("Fetching from free backup job board (Remotive API)...")
        for bq in ["SOC analyst", "security operations", "GRC", "vulnerability analyst", "penetration testing", "red team", "QA", "Support", "Security"]:
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
            return client.models.generate_content(
                model=model_name,
                contents=prompt
            )
        except Exception as e:
            print(f"  Model {model_name} unavailable ({e}). Trying next...")

    raise RuntimeError("Could not find an active Gemini model for this API key.")

def evaluate_with_gemini(raw_jobs):
    gemini_key = "".join((os.getenv("GEMINI_API_KEY") or "").split())
    if not gemini_key:
        raise ValueError("GEMINI_API_KEY secret is missing in GitHub Secrets!")

    client = genai.Client(api_key=gemini_key)

    compact_jobs = []
    for job in raw_jobs[:25]:
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

    Candidate background and target roles:
    {USER_PROFILE}

    Raw job postings retrieved today:
    {compact_jobs}

    Screening and ranking rules:
    1. Cybersecurity is the candidate's primary goal. Prioritize genuine entry-level cybersecurity openings, especially SOC Analyst L1/Tier 1, Security Operations Analyst, Junior Security Analyst, junior GRC/compliance/risk/audit, and junior vulnerability-management roles. QA, CSV, IT support, and network support are secondary alternatives, not substitutes when suitable entry-level cybersecurity jobs are available.
    2. Accept roles described as intern, trainee, graduate, junior, associate, L1/Tier 1, or entry-level. Prefer 0-2 years of required experience. A clearly junior role asking for up to 3 years can still be considered if the listed duties and requirements fit the candidate. Reject postings requiring 4+ years or describing senior, lead, manager, architect, or advanced responsibilities.
    3. Treat red-team and penetration-testing roles especially strictly: include only genuinely entry-level internships, trainee, graduate, or junior openings. Exclude roles requiring substantial professional offensive-security experience, advanced certifications, or skills absent from the candidate profile. Do not invent qualifications or imply that foundational exposure equals professional experience.
    4. Review the actual requirements and responsibilities, not just keywords in the title. Do not recommend a role if its experience, certification, or technical requirements clearly exceed the profile. Be honest about gaps and score match accordingly. Do not invent candidate experience, certifications, tools, or skills.
    5. Select the best 5 to 10 suitable jobs. If fewer than 5 genuinely suitable jobs exist, show only the suitable results rather than padding with poor matches. Rank strong entry-level cybersecurity matches first.
    6. Format the output as a clean, responsive HTML email digest using inline CSS and a modern dark navy/slate theme (#0f172a headers, #f8fafc card backgrounds, #2563eb buttons).
       For each job card include:
         * Job Title & Company Name
         * Location & Work Mode (highlight Remote or Global)
         * Match Score & one-sentence explanation grounded in the posting and candidate profile
         * A clickable application link. If apply_link_is_direct is true, label it "Direct Apply"; otherwise label it "View Job / Apply" and do not claim it is the employer's direct career page.
         * Source platform/publisher, using only what is supplied in the job data
         * A separate "Company Careers Page" link only if a company_career_page URL is supplied
         * "Public HR Contact" only when a public HR email or phone is supplied; otherwise write "Public HR contact not available"
       Use only URLs, emails, phone numbers, and publisher names explicitly present in the supplied job data. NEVER invent or guess a company career URL, HR contact, recruiter identity, or phone number. Do not present a job-board redirect as a direct company application link. Keep the existing email styling and candidate targeting.

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
    """Writes the HTML job digest directly to the GitHub Actions run summary screen."""
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
    # Additional recipient receives the digest as CC. Override with EMAIL_CC if needed.
    cc = "".join((os.getenv("EMAIL_CC") or "harshangpatel@alembic.co.in").split())

    if not all([sender, password, receiver]):
        raise ValueError(
            "Missing EMAIL_SENDER, EMAIL_APP_PASSWORD, or EMAIL_RECEIVER in GitHub Secrets."
        )
    if len(password) != 16:
        raise ValueError(
            "EMAIL_APP_PASSWORD should be the 16-character Google App Password "
            "(remove spaces; do not use your normal Gmail password)."
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Morning IT Job Digest | Curated by Gemini"
    msg["From"] = sender
    msg["To"] = receiver
    if cc:
        msg["Cc"] = cc
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    # Include CC recipients in the SMTP envelope as well as the message header.
    recipients = [receiver] + ([cc] if cc and cc.lower() != receiver.lower() else [])

    failures = []

    # Port 587: SMTP followed by STARTTLS.
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

    # Port 465: implicit TLS fallback.
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

    raise RuntimeError(
        "Gmail could not send the digest using either SMTP connection. "
        "Check that EMAIL_SENDER is the Gmail account that created the App Password, "
        "2-Step Verification is enabled, and EMAIL_APP_PASSWORD is a current "
        "16-character Google App Password. Do not use the normal Gmail password. "
        + " | ".join(failures)
    )

def main():
    print("Step 1: Searching for recent entry-level cybersecurity, QA, CSV, and support jobs...")
    jobs = fetch_jobs()
    print(f"Found {len(jobs)} unique raw postings.")

    if not jobs:
        print("No job postings found from primary or backup sources.")
        return

    print("Step 2: Scoring and filtering postings with Gemini...")
    digest_html = evaluate_with_gemini(jobs)

    # Always publish the job cards to the GitHub Actions Summary screen first
    publish_to_github_summary(digest_html)

    print("Step 3: Delivering your morning email alert...")
    send_email_digest(digest_html)

if __name__ == "__main__":
    main()

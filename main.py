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

Target Roles to Hunt:
1. QA Test Engineer / Manual Tester / Software Test Engineer / SDET (Junior)
2. QA IT Executive / CSV (Computer System Validation) / IT Compliance in Pharma or Healthcare
3. Application Support / IT Support / Technical Support Engineer
4. Cybersecurity Analyst (Entry-level / L1) / Network Support Engineer

Target Locations: Pan-India (Vadodara, Ahmedabad, Pune, Bengaluru, Mumbai, Hyderabad, Remote) AND Global / Worldwide opportunities.
Exclude: High-seniority roles requiring 5+ years of experience, non-technical BPO, and pure sales.
"""

SEARCH_QUERIES = [
    "QA Tester in India",
    "Application Support Engineer in India",
    "Computer System Validation QA in India",
    "Network Engineer in India"
]

def extract_job_list(payload):
    """Safely extracts the list of job dicts from JSearch v1 or v2 responses."""
    raw_data = payload.get("data", [])
    if isinstance(raw_data, list):
        return [j for j in raw_data if isinstance(j, dict)]
    if isinstance(raw_data, dict):
        for key in ("jobs", "results", "data", "items"):
            if isinstance(raw_data.get(key), list):
                return [j for j in raw_data[key] if isinstance(j, dict)]
        for val in raw_data.values():
            if isinstance(val, list):
                return [j for j in val if isinstance(j, dict)]
    return []

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
        for bq in ["QA", "Support", "Security"]:
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
            "description": (job.get("job_description") or job.get("description") or "")[:600],
            "apply_link": job.get("job_apply_link") or job.get("apply_link") or job.get("url")
        })

    prompt = f"""
    You are Harshang Patel's dedicated AI Career Agent.

    Here is Harshang's technical background, resume details, and target preferences:
    {USER_PROFILE}

    Here are the raw IT job postings retrieved today:
    {compact_jobs}

    Tasks:
    1. Evaluate each job against Harshang's qualifications (QA manual/functional/UAT, Pharma validation/GAMP 5, API/Postman, Application/IT support, entry-level network & cyber security).
    2. Select the best 5 to 10 matching openings.
    3. Format the final output as a clean, responsive HTML email digest using inline CSS.
       - Use a modern dark navy/slate theme (#0f172a headers, #f8fafc card backgrounds, #2563eb buttons).
       - For each job card include:
         * Job Title & Company Name
         * Location & Work Mode (Highlight if Remote or Global)
         * Match Score & 1-sentence explanation of why it fits Harshang's CV
         * A clickable "Apply Now" button styled as an HTML link.

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
    # Strip all spaces, tabs, and hidden newlines from credentials
    sender = "".join((os.getenv("EMAIL_SENDER") or "").split())
    password = "".join((os.getenv("EMAIL_APP_PASSWORD") or "").split())
    receiver = "".join((os.getenv("EMAIL_RECEIVER") or "").split())

    print(f"SMTP Check -> Sender: {sender} | Receiver: {receiver} | App Password Length: {len(password)} chars")

    if not all([sender, password, receiver]):
        raise ValueError("Missing EMAIL_SENDER, EMAIL_APP_PASSWORD, or EMAIL_RECEIVER in GitHub Secrets!")

    if len(password) != 16:
        print(f"WARNING: Google App Passwords are exactly 16 characters, but yours has {len(password)} characters.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Morning IT Job Digest | Curated by Gemini"
    msg["From"] = sender
    msg["To"] = receiver
    msg.attach(MIMEText(html_content, "html"))

    # Use Port 587 (STARTTLS) with a 30s timeout to prevent hanging on GitHub runners
    try:
        print("Connecting to smtp.gmail.com on port 587 (STARTTLS)...")
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(sender, password)
            server.sendmail(sender, receiver, msg.as_string())
        print(f"SUCCESS: Digest email sent to {receiver}!")
    except Exception as e:
        print(f"Port 587 failed ({e}). Trying fallback port 465 (SSL)...")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.ehlo()
            server.login(sender, password)
            server.sendmail(sender, receiver, msg.as_string())
        print(f"SUCCESS: Digest email sent to {receiver} via port 465!")

def main():
    print("Step 1: Searching for recent IT, QA, CSV, and Support job postings...")
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

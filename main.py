import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv
from google import genai
import requests

load_dotenv()

# Profile tailored to your resume
USER_PROFILE = """
Candidate: Harshang Patel (harshangpatel4774@gmail.com)
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
4. Cybersecurity Analyst (Entry-level / L1) / Information Security Associate
5. Network Engineer / Network Support / Systems Associate

Target Locations: Pan-India (Vadodara, Ahmedabad, Pune, Bengaluru, Mumbai, Hyderabad, Remote) AND Global / Worldwide / Relocation-friendly opportunities.
Exclude: High-seniority roles requiring 5+ years of experience, non-technical BPO, and pure sales.
"""

SEARCH_QUERIES = [
    "QA Tester OR Test Engineer India",
    "Application Support Engineer OR IT Support India",
    "Network Engineer OR Junior Cyber Security India",
    "Computer System Validation Pharma QA",
    "Junior QA Engineer Remote Worldwide"
]

def fetch_jobs():
    rapidapi_key = os.getenv("RAPIDAPI_KEY")
    if not rapidapi_key:
        print("Error: RAPIDAPI_KEY is not set.")
        return []

    url = "https://jsearch.p.rapidapi.com/search"
    headers = {
        "X-RapidAPI-Key": rapidapi_key,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com"
    }

    all_jobs = []
    seen_ids = set()

    for q in SEARCH_QUERIES:
        params = {
            "query": q,
            "page": "1",
            "num_pages": "1",
            "date_posted": "today"
        }
        try:
            response = requests.get(url, headers=headers, params=params, timeout=15)
            if response.status_code == 200:
                data = response.json().get("data", [])
                for job in data:
                    job_id = job.get("job_id")
                    if job_id and job_id not in seen_ids:
                        seen_ids.add(job_id)
                        all_jobs.append(job)
        except Exception as e:
            print(f"Error fetching query '{q}': {e}")

    return all_jobs

def evaluate_with_gemini(raw_jobs):
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    compact_jobs = []
    for job in raw_jobs[:25]:
        compact_jobs.append({
            "title": job.get("job_title"),
            "company": job.get("employer_name"),
            "location": f"{job.get('job_city', '')}, {job.get('job_country', '')}",
            "is_remote": job.get("job_is_remote"),
            "description": (job.get("job_description") or "")[:600],
            "apply_link": job.get("job_apply_link")
        })

    prompt = f"""
    You are Harshang Patel's dedicated AI Career Agent.

    Here is Harshang's technical background, resume details, and target preferences:
    {USER_PROFILE}

    Here are the raw IT job postings retrieved from the last 24 hours:
    {compact_jobs}

    Tasks:
    1. Evaluate each job carefully against Harshang's qualifications (QA manual/functional/UAT, Pharma validation/GAMP 5, API/Postman, Application/IT support, entry-level network & cyber security).
    2. Discard any roles requiring 5+ years experience, non-technical positions, or completely unrelated stacks.
    3. Select the best 5 to 10 matching openings.
    4. Format the final output as a clean, responsive HTML email digest using inline CSS.
       - Use a modern dark navy/slate theme (#0f172a headers, #f8fafc card backgrounds, #2563eb buttons).
       - For each job card include:
         * Job Title & Company Name
         * Location & Work Mode (Highlight if Remote or Global)
         * Match Score & 1-sentence explanation of why it fits Harshang's CV
         * A clickable "Apply Now" button styled as an HTML link.
    5. If zero jobs qualify, create a brief, polite HTML card stating no suitable jobs met the threshold today.

    CRITICAL: Output ONLY the raw HTML string. Do NOT wrap it in markdown code blocks or triple backticks.
    """

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    
    html_output = response.text.strip()
    if html_output.startswith("```html"):
        html_output = html_output[7:]
    if html_output.endswith("```"):
        html_output = html_output[:-3]

    return html_output.strip()

def send_email_digest(html_content):
    sender = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_APP_PASSWORD")
    receiver = os.getenv("EMAIL_RECEIVER")

    if not all([sender, password, receiver]):
        print("Missing email configuration in environment variables.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Morning IT Job Digest | Curated by Gemini"
    msg["From"] = sender
    msg["To"] = receiver
    msg.attach(MIMEText(html_content, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, receiver, msg.as_string())
    print(f"Digest email sent successfully to {receiver}!")

def main():
    print("Step 1: Searching for recent IT, QA, CSV, and Support job postings...")
    jobs = fetch_jobs()
    print(f"Found {len(jobs)} unique raw postings.")

    if not jobs:
        print("No job postings found today. Skipping email.")
        return

    print("Step 2: Scoring and filtering postings with Gemini...")
    digest_html = evaluate_with_gemini(jobs)

    print("Step 3: Delivering your morning email alert...")
    send_email_digest(digest_html)

if __name__ == "__main__":
    main()

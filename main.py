import os
import json
import time
import threading
import logging
from datetime import datetime
import feedparser
import requests
from flask import Flask
from waitress import serve
import re

# --- Configuration ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID", "YOUR_CHAT_ID")

SEARCH_KEYWORDS = [
    "junior doctor", "junior clinical fellow", "clinical fellow", "medical fellow",
    "foundation year 1", "foundation year 2", "foundation house officer 1",
    "foundation house officer 2", "FY1", "FY2", "senior house officer", "SHO",
    "trust doctor", "trust grade doctor", "resident medical officer", "RMO"
]

NHS_JOBS_URL = "https://www.jobs.nhs.uk/candidate/search/results?keyword={}&field=title&location=UK&sort=publicationDate&jobPostType=all&payBand=all&workArrangement=all&rss=1"

DB_FILE = "seen_jobs.json"

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# --- Flask Web Server for Hosting ---
app = Flask(__name__)

@app.route('/')
def index():
    return "Junior Doctor Bot is running!"

# --- Helper functions ---

def load_seen_jobs():
    if not os.path.exists(DB_FILE):
        return set()
    try:
        with open(DB_FILE, 'r') as f:
            data = json.load(f)
            return set(data)
    except (json.JSONDecodeError, IOError) as e:
        logging.warning(f"Could not read database file {DB_FILE}. Starting fresh. Error: {e}")
        return set()

def save_seen_jobs(seen_jobs):
    try:
        with open(DB_FILE, 'w') as f:
            json.dump(list(seen_jobs), f, indent=4)
    except IOError as e:
        logging.error(f"Failed to save sent jobs to {DB_FILE}: {e}")

def send_telegram_message(message):
    if BOT_TOKEN == "YOUR_BOT_TOKEN" or CHAT_ID == "YOUR_CHAT_ID":
        logging.warning("Bot Token or Chat ID is not configured. Skipping message send.")
        return False

    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': CHAT_ID,
        'text': message,
        'parse_mode': 'Markdown',
        'disable_web_page_preview': True
    }
    try:
        response = requests.post(api_url, json=payload, timeout=10)
        response.raise_for_status()
        logging.info(f"Successfully sent message to chat {CHAT_ID}.")
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send Telegram message: {e}")
        return False

def parse_date(entry):
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        return datetime.fromtimestamp(time.mktime(entry.published_parsed)).strftime('%d %b %Y')
    if hasattr(entry, 'published'):
        try:
            dt = datetime.strptime(entry.published, '%a, %d %b %Y %H:%M:%S %Z')
            return dt.strftime('%d %b %Y')
        except ValueError:
            return "N/A"
    return "N/A"

def sanitize_feed_content(content):
    """Remove invalid XML characters that can break feedparser."""
    # Remove non-printable and invalid XML chars
    return re.sub(
        '[^\x09\x0A\x0D\x20-\x7F\x80-\xFF]', '', content.decode('utf-8', errors='ignore')
    ).encode('utf-8')

def fetch_and_process_feed(feed_url, seen_jobs, source_name):
    new_jobs_found_count = 0
    headers = {
        'User-Agent': 'UKJuniorDoctorBot/1.0'
    }
    try:
        logging.info(f"Fetching {source_name} from {feed_url}...")
        response = requests.get(feed_url, headers=headers, timeout=15)
        response.raise_for_status()

        # --- SANITIZE FEED ---
        clean_content = sanitize_feed_content(response.content)

        feed = feedparser.parse(clean_content)
        if feed.bozo:
            logging.warning(f"Warning processing {source_name}: Malformed feed data - {feed.bozo_exception}")

        for entry in reversed(feed.entries):
            job_id = entry.get('id', entry.link)
            if job_id not in seen_jobs:
                job_title = entry.title.strip()
                job_link = entry.link
                published_date = parse_date(entry)

                if any(keyword.lower() in job_title.lower() for keyword in SEARCH_KEYWORDS):
                    message = f"🩺 **{job_title}**\n📅 Published: {published_date}\n🔗 [Link to apply]({job_link})"
                    if send_telegram_message(message):
                        seen_jobs.add(job_id)
                        new_jobs_found_count += 1
                        time.sleep(2)

    except requests.exceptions.Timeout:
        logging.error(f"Timeout error fetching {source_name} at {feed_url}.")
    except requests.exceptions.RequestException as e:
        logging.error(f"Network error fetching {source_name}: {e}")
    except Exception as e:
        logging.error(f"Unexpected error processing {source_name}: {e}", exc_info=True)

    return new_jobs_found_count > 0

def check_for_new_jobs():
    seen_jobs = load_seen_jobs()
    any_new_jobs = False
    logging.info("--- Starting new job check cycle ---")

    # NHS Jobs
    logging.info("--- Checking NHS Jobs ---")
    for keyword in SEARCH_KEYWORDS:
        nhs_url = NHS_JOBS_URL.format(requests.utils.quote(keyword))
        if fetch_and_process_feed(nhs_url, seen_jobs, f"NHS Jobs ('{keyword}')"):
            any_new_jobs = True

    # HealthJobsUK
    logging.info("--- Checking HealthJobsUK ---")
    for keyword in SEARCH_KEYWORDS:
        hjuk_url = f"https://www.healthjobsuk.com/job_search/rss?keyword={requests.utils.quote(keyword)}"
        if fetch_and_process_feed(hjuk_url, seen_jobs, f"HealthJobsUK ('{keyword}')"):
            any_new_jobs = True

    if any_new_jobs:
        save_seen_jobs(seen_jobs)
        logging.info("New jobs were found and sent. Database updated.")
    else:
        logging.info("No new jobs found this cycle.")

def continuous_job_checker():
    while True:
        try:
            check_for_new_jobs()
        except Exception as e:
            logging.critical(f"Unhandled error in job checker loop: {e}")
        sleep_duration = 300
        logging.info(f"Check cycle complete. Sleeping for {sleep_duration/60:.0f} minutes.")
        time.sleep(sleep_duration)

if __name__ == "__main__":
    # Run job checker in a separate daemon thread
    threading.Thread(target=continuous_job_checker, daemon=True).start()
    logging.info("Starting Flask server...")

    # Run Flask with Waitress (production-ready)
    serve(app, host='0.0.0.0', port=10000)

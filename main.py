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

# --- Persistence & State Management ---
DB_FILE = "seen_jobs.json"

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# --- Flask Web Server ---
app = Flask(__name__)

@app.route('/')
def index():
    return "🩺 Junior Doctor Bot is running!"

# --- Helper Functions ---

def load_seen_jobs():
    if not os.path.exists(DB_FILE):
        return set()
    try:
        with open(DB_FILE, 'r') as f:
            data = json.load(f)
            return set(data)
    except (json.JSONDecodeError, IOError) as e:
        logging.warning(f"Could not read {DB_FILE}. Starting fresh. Error: {e}")
        return set()

def save_seen_jobs(seen_jobs):
    try:
        with open(DB_FILE, 'w') as f:
            json.dump(list(seen_jobs), f, indent=4)
    except IOError as e:
        logging.error(f"Failed to save {DB_FILE}: {e}")

def send_telegram_message(message):
    if BOT_TOKEN == "YOUR_BOT_TOKEN" or CHAT_ID == "YOUR_CHAT_ID":
        logging.warning("BOT_TOKEN or CHAT_ID not set. Skipping message.")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': CHAT_ID,
        'text': message,
        'parse_mode': 'Markdown',
        'disable_web_page_preview': True
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logging.info("Message sent successfully.")
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send Telegram message: {e}")
        return False

def parse_date(entry):
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        return datetime.fromtimestamp(time.mktime(entry.published_parsed)).strftime('%d %b %Y')
    return "N/A"

def fetch_and_process_feed(feed_url, seen_jobs, source_name):
    new_jobs_found = False
    try:
        logging.info(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching {source_name}...")
        response = requests.get(feed_url, timeout=15)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        if feed.bozo:
            logging.warning(f"Malformed feed from {source_name}: {feed.bozo_exception}")
        for entry in reversed(feed.entries):
            job_id = entry.get('id', entry.link)
            if job_id not in seen_jobs:
                job_title = entry.title.strip()
                job_link = entry.link
                published_date = parse_date(entry)
                message = f"🩺 **{job_title}**\n📅 Published: {published_date}\n🔗 [Apply Here]({job_link})"
                if send_telegram_message(message):
                    seen_jobs.add(job_id)
                    new_jobs_found = True
                    time.sleep(2)
    except Exception as e:
        logging.error(f"Error fetching {source_name}: {e}")
    return new_jobs_found

def check_for_new_jobs():
    seen_jobs = load_seen_jobs()
    logging.info(f"[{datetime.now().strftime('%H:%M:%S')}] --- Starting job check cycle ---")
    any_new_jobs = False

    # NHS Jobs
    logging.info("--- Checking NHS Jobs ---")
    for keyword in SEARCH_KEYWORDS:
        url = NHS_JOBS_URL.format(requests.utils.quote(keyword))
        if fetch_and_process_feed(url, seen_jobs, f"NHS Jobs ('{keyword}')"):
            any_new_jobs = True

    if any_new_jobs:
        save_seen_jobs(seen_jobs)
        logging.info(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ New jobs sent. Database updated.")
    else:
        logging.info(f"[{datetime.now().strftime('%H:%M:%S')}] No new jobs found this cycle.")

def continuous_job_checker():
    while True:
        try:
            check_for_new_jobs()
        except Exception as e:
            logging.critical(f"Unhandled error in job loop: {e}", exc_info=True)
        logging.info(f"[{datetime.now().strftime('%H:%M:%S')}] Cycle complete. Sleeping 5 minutes.")
        time.sleep(300)  # 5 minutes

if __name__ == "__main__":
    # Start job checker in background thread
    threading.Thread(target=continuous_job_checker, daemon=True).start()
    logging.info(f"[{datetime.now().strftime('%H:%M:%S')}] Flask server starting...")
    serve(app, host='0.0.0.0', port=10000)

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
# These are loaded from environment variables on Render for security.
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID", "YOUR_CHAT_ID")

# --- Job Search & Filtering ---
# Keywords to find relevant junior doctor roles
SEARCH_KEYWORDS = [
    "junior doctor", "junior clinical fellow", "clinical fellow", "medical fellow",
    "foundation year 1", "foundation year 2", "foundation house officer 1",
    "foundation house officer 2", "FY1", "FY2", "senior house officer", "SHO",
    "trust doctor", "trust grade doctor", "resident medical officer", "RMO"
]

# RSS Feeds to monitor
NHS_JOBS_URL = "https://www.jobs.nhs.uk/candidate/search/results?keyword={}&field=title&location=UK&sort=publicationDate&jobPostType=all&payBand=all&workArrangement=all&rss=1"
HEALTHJOBSUK_URL = "https://www.healthjobsuk.com/rss/jobs?job_title={}"

# --- Persistence & State Management ---
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
    """Root endpoint to confirm the bot is running."""
    return "Junior Doctor Bot is running!"

# --- Core Bot Logic ---

def load_seen_jobs():
    """Loads the set of previously sent job links/IDs from the local JSON file."""
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
    """Saves the updated set of sent job links/IDs to the local JSON file."""
    try:
        with open(DB_FILE, 'w') as f:
            json.dump(list(seen_jobs), f, indent=4)
    except IOError as e:
        logging.error(f"Failed to save sent jobs to {DB_FILE}: {e}")

def send_telegram_message(message):
    """Sends a formatted message to the specified Telegram chat."""
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
        response.raise_for_status()  # Raises an exception for HTTP errors
        logging.info(f"Successfully sent message to chat {CHAT_ID}.")
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send Telegram message: {e}")
        return False

def parse_date(entry):
    """Parses the publication date from a feed entry, handling various formats."""
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        return datetime.fromtimestamp(time.mktime(entry.published_parsed)).strftime('%d %b %Y')
    if hasattr(entry, 'published'):
        try:
            dt = datetime.strptime(entry.published, '%a, %d %b %Y %H:%M:%S %Z')
            return dt.strftime('%d %b %Y')
        except ValueError:
            return "N/A" # Fallback if parsing fails
    return "N/A"

def fetch_and_process_feed(feed_url, seen_jobs, source_name):
    """Fetches a single RSS feed with a timeout and processes new entries."""
    new_jobs_found_count = 0
    headers = {
        'User-Agent': 'UKJuniorDoctorBot/1.0; (by /u/YourUsername; an automated job scraper)'
    }
    
    try:
        # Step 1: Fetch the feed content with a strict timeout
        logging.info(f"Fetching {source_name} from {feed_url}...")
        response = requests.get(feed_url, headers=headers, timeout=15) # 15-second timeout
        response.raise_for_status() # Raise an exception for bad status codes (like 404 or 500)
        content = response.content
        logging.info(f"Successfully fetched content for {source_name}.")

        # Step 2: Parse the fetched content
        feed = feedparser.parse(content)
        if feed.bozo:
            logging.warning(f"Warning processing {source_name}: Malformed feed data - {feed.bozo_exception}")

        # Process entries from oldest to newest to send in chronological order
        for entry in reversed(feed.entries):
            job_id = entry.get('id', entry.link)
            if job_id not in seen_jobs:
                job_title = entry.title.strip()
                job_link = entry.link
                published_date = parse_date(entry)

                message = (
                    f"🩺 **{job_title}**\n"
                    f"📅 Published: {published_date}\n"
                    f"🔗 [Link to apply]({job_link})"
                )

                if send_telegram_message(message):
                    seen_jobs.add(job_id)
                    new_jobs_found_count += 1
                    time.sleep(2) # Stagger messages to avoid Telegram rate limits

    except requests.exceptions.Timeout:
        logging.error(f"Timeout error when fetching {source_name} at {feed_url}. The server took too long to respond.")
    except requests.exceptions.RequestException as e:
        logging.error(f"Network error when fetching {source_name}: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred while processing {source_name}: {e}")

    return new_jobs_found_count > 0

def check_for_new_jobs():
    """Main function to iterate through keywords and feeds to find new jobs."""
    seen_jobs = load_seen_jobs()
    any_new_jobs = False

    logging.info("Starting new job check cycle...")

    for keyword in SEARCH_KEYWORDS:
        # 1. NHS Jobs
        nhs_url = NHS_JOBS_URL.format(requests.utils.quote(keyword))
        if fetch_and_process_feed(nhs_url, seen_jobs, f"NHS Jobs ('{keyword}')"):
            any_new_jobs = True

        # 2. HealthJobsUK
        hjuk_url = HEALTHJOBSUK_URL.format(requests.utils.quote(keyword))
        if fetch_and_process_feed(hjuk_url, seen_jobs, f"HealthJobsUK ('{keyword}')"):
            any_new_jobs = True

    if any_new_jobs:
        save_seen_jobs(seen_jobs)
        logging.info("New jobs found and sent. Database updated.")
    else:
        logging.info("No new jobs found in this cycle.")

def continuous_job_checker():
    """Runs the job check function in a loop with a delay."""
    while True:
        try:
            check_for_new_jobs()
        except Exception as e:
            logging.critical(f"An unhandled error occurred in the job checker loop: {e}")
        
        # Check every 5 minutes (300 seconds)
        sleep_duration = 300
        logging.info(f"Check cycle complete. Sleeping for {sleep_duration / 60:.0f} minutes.")
        time.sleep(sleep_duration)

if __name__ == "__main__":
    # Start the job checker in a background thread
    checker_thread = threading.Thread(target=continuous_job_checker, daemon=True)
    checker_thread.start()

    # Run the Flask app using Waitress, a production-ready server
    logging.info("Starting Flask server...")

    serve(app, host='0.0.0.0', port=10000)



#!/usr/bin/env python3
"""
Common utilities for all scrapers.
Provides rate limiting, retries, file operations, and logging setup.
"""

import time
import random
import json
import csv
import logging
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
from functools import wraps
from datetime import datetime


# =============================================================================
# RATE LIMITING
# =============================================================================

class RateLimiter:
    """
    Rate limiter with jitter to avoid detection patterns.
    Implements adaptive delays and request tracking.
    """

    def __init__(self, min_delay: float = 2.0, max_delay: float = 5.0,
                 requests_per_minute: int = 15):
        """
        Initialize rate limiter.

        Args:
            min_delay: Minimum delay between requests (seconds)
            max_delay: Maximum delay between requests (seconds)
            requests_per_minute: Maximum requests per minute threshold
        """
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.requests_per_minute = requests_per_minute
        self.request_times: List[float] = []

    def wait(self):
        """Wait with jittered delay and enforce rate limits."""
        # Add jitter to avoid pattern detection
        delay = random.uniform(self.min_delay, self.max_delay)

        # Check if we're exceeding rate limit
        now = time.time()
        self.request_times = [t for t in self.request_times if now - t < 60]

        if len(self.request_times) >= self.requests_per_minute:
            # Wait until oldest request falls outside 1-minute window
            oldest = self.request_times[0]
            wait_time = 60 - (now - oldest)
            if wait_time > 0:
                logging.warning(f"Rate limit reached. Waiting {wait_time:.1f}s")
                time.sleep(wait_time + delay)
        else:
            time.sleep(delay)

        self.request_times.append(time.time())

    def adaptive_wait(self, error_count: int = 0):
        """
        Adaptive wait that increases delay based on errors.

        Args:
            error_count: Number of consecutive errors encountered
        """
        if error_count > 0:
            # Exponential backoff for errors
            backoff_delay = min(self.max_delay * (2 ** error_count), 300)  # Max 5 min
            logging.warning(f"Adaptive backoff: {backoff_delay:.1f}s (errors: {error_count})")
            time.sleep(backoff_delay)
        else:
            self.wait()


# =============================================================================
# RETRY LOGIC
# =============================================================================

def retry_on_exception(max_retries: int = 3,
                       backoff_base: float = 2.0,
                       exceptions: tuple = (Exception,)):
    """
    Decorator for retrying functions with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts
        backoff_base: Base for exponential backoff calculation
        exceptions: Tuple of exception types to catch and retry
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if attempt < max_retries:
                        wait_time = backoff_base ** attempt
                        logging.warning(
                            f"{func.__name__} failed (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                            f"Retrying in {wait_time:.1f}s..."
                        )
                        time.sleep(wait_time)
                    else:
                        logging.error(
                            f"{func.__name__} failed after {max_retries + 1} attempts: {e}"
                        )

            raise last_exception

        return wrapper
    return decorator


# =============================================================================
# FILE OPERATIONS
# =============================================================================

def append_jsonl(file_path: Path, record: Dict):
    """
    Atomically append a single JSONL record to file.
    Creates parent directories if needed.

    Args:
        file_path: Path to JSONL file
        record: Dictionary to append as JSON line
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, 'a', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False)
        f.write('\n')


def append_jsonl_batch(file_path: Path, records: List[Dict]):
    """
    Atomically append multiple JSONL records.

    Args:
        file_path: Path to JSONL file
        records: List of dictionaries to append
    """
    if not records:
        return

    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, 'a', encoding='utf-8') as f:
        for record in records:
            json.dump(record, f, ensure_ascii=False)
            f.write('\n')


def read_jsonl(file_path: Path) -> List[Dict]:
    """
    Read all records from a JSONL file.

    Args:
        file_path: Path to JSONL file

    Returns:
        List of dictionaries
    """
    if not file_path.exists():
        return []

    records = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as e:
                    logging.warning(f"Invalid JSON line in {file_path}: {e}")

    return records


def jsonl_to_csv(jsonl_path: Path, csv_path: Path,
                 fieldnames: Optional[List[str]] = None):
    """
    Convert JSONL file to CSV format.

    Args:
        jsonl_path: Source JSONL file path
        csv_path: Destination CSV file path
        fieldnames: List of field names (auto-detected if None)
    """
    records = read_jsonl(jsonl_path)

    if not records:
        logging.warning(f"No records to convert from {jsonl_path}")
        return

    # Auto-detect fieldnames from first record if not provided
    if fieldnames is None:
        fieldnames = list(records[0].keys())

    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()

        for record in records:
            # Flatten nested objects for CSV
            flat_record = {}
            for key, value in record.items():
                if isinstance(value, (dict, list)):
                    flat_record[key] = json.dumps(value, ensure_ascii=False)
                else:
                    flat_record[key] = value

            writer.writerow(flat_record)

    logging.info(f"Converted {len(records)} records to {csv_path}")


def load_json_file(file_path: Path) -> Dict:
    """
    Load JSON file with error handling.

    Args:
        file_path: Path to JSON file

    Returns:
        Parsed JSON as dictionary
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logging.error(f"File not found: {file_path}")
        raise
    except json.JSONDecodeError as e:
        logging.error(f"Invalid JSON in {file_path}: {e}")
        raise


def save_json_file(file_path: Path, data: Dict, indent: int = 2):
    """
    Save dictionary to JSON file with pretty printing.

    Args:
        file_path: Path to JSON file
        data: Dictionary to save
        indent: JSON indentation level
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)


# =============================================================================
# LOGGING SETUP
# =============================================================================

def setup_logging(log_file: Path, level: int = logging.INFO):
    """
    Configure structured logging with file and console handlers.

    Args:
        log_file: Path to log file
        level: Logging level (default: INFO)
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # File handler
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers to avoid duplicates
    root_logger.handlers.clear()

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logging.info(f"Logging initialized. Writing to {log_file}")


# =============================================================================
# UTILITIES
# =============================================================================

def normalize_product_name(name: str) -> str:
    """
    Normalize product name for deduplication.
    Lowercase, strip whitespace, remove extra spaces.

    Args:
        name: Product name

    Returns:
        Normalized name
    """
    if not name:
        return ""

    return ' '.join(name.lower().strip().split())


def parse_price(price_str: str) -> Optional[float]:
    """
    Parse price string to float, handling various formats.

    Args:
        price_str: Price string (e.g., "$4.99", "4,99 €")

    Returns:
        Parsed price as float, or None if parsing fails
    """
    if not price_str:
        return None

    try:
        # Remove currency symbols and common formatting
        cleaned = price_str.replace('$', '').replace('€', '').replace('£', '')
        cleaned = cleaned.replace(',', '.').strip()

        return float(cleaned)
    except (ValueError, AttributeError):
        logging.warning(f"Failed to parse price: {price_str}")
        return None


def get_iso_timestamp() -> str:
    """
    Get current timestamp in ISO 8601 format.

    Returns:
        ISO formatted timestamp string
    """
    return datetime.utcnow().isoformat() + 'Z'


def ensure_data_directories(project_root: Path):
    """
    Create required data directory structure.

    Args:
        project_root: Root directory of the project
    """
    dirs = [
        project_root / 'data' / 'raw',
        project_root / 'data' / 'logs',
        project_root / 'data' / 'checkpoints',
    ]

    for dir_path in dirs:
        dir_path.mkdir(parents=True, exist_ok=True)

    logging.debug(f"Ensured data directories exist under {project_root / 'data'}")

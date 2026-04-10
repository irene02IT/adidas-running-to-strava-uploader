#!/usr/bin/env python3
"""
Strava GPX Upload Tool

Upload GPX files from Runtastic to Strava with automatic activity type detection
and gear management.
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import requests

# Strava API endpoints
STRAVA_UPLOAD_URL = "https://www.strava.com/api/v3/uploads"
STRAVA_UPLOAD_STATUS_URL = "https://www.strava.com/api/v3/uploads/{upload_id}"
STRAVA_ACTIVITY_URL = "https://www.strava.com/api/v3/activities/{activity_id}"

# Mapping from Runtastic sport_type_id to Strava activity type
RUNTASTIC_TO_STRAVA_TYPE = {
    1: "Run",
    2: "Walk",
    3: "Ride",
    4: "Ride",
    7: "Walk",
    13: "Hike",
    15: "Ride",
    18: "Swim",
    19: "Walk",
    22: "Ride",
    82: "Trail Run",
}

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


class StravaAPIError(Exception):
    """Custom exception for Strava API errors."""
    pass


class StravaUploader:
    """Handle Strava GPX uploads with retry logic and gear management."""
    
    def __init__(self, access_token: str, verbose: bool = False):
        """
        Initialize the Strava uploader.
        
        Args:
            access_token: Strava OAuth access token
            verbose: Enable verbose logging
        """
        self.access_token = access_token
        self.headers = {"Authorization": f"Bearer {access_token}"}
        
        if verbose:
            logger.setLevel(logging.DEBUG)
    
    def upload_gpx(
        self,
        gpx_path: Path,
        activity_type: str = "Run",
        name: Optional[str] = None,
        description: Optional[str] = None,
        private: bool = True,
        trainer: bool = False,
        commute: bool = False,
    ) -> dict:
        """
        Upload a GPX file to Strava.
        
        Args:
            gpx_path: Path to the GPX file
            activity_type: Type of activity (Run, Ride, Walk, etc.)
            name: Activity name (auto-generated if None)
            description: Activity description (auto-generated if None)
            private: Whether the activity should be private
            trainer: Whether this is a trainer activity
            commute: Whether this is a commute
            
        Returns:
            dict: Upload response from Strava API
            
        Raises:
            FileNotFoundError: If GPX file doesn't exist
            StravaAPIError: If API request fails
        """
        if not gpx_path.exists():
            raise FileNotFoundError(f"GPX file not found: {gpx_path}")

        # Auto-generate name and description if not provided
        if name is None:
            name = f"Imported {activity_type} activity"
        if description is None:
            description = f"Imported {activity_type} activity from Runtastic"

        data = {
            "data_type": "gpx",
            "activity_type": activity_type,
            "name": name,
            "private": int(private),
            "trainer": int(trainer),
            "commute": int(commute),
            # Note: gear_id cannot be set during upload, must be cleared via update API
        }

        if description:
            data["description"] = description

        try:
            with gpx_path.open("rb") as gpx_file:
                files = {"file": (gpx_path.name, gpx_file, "application/gpx+xml")}
                response = requests.post(
                    STRAVA_UPLOAD_URL, 
                    headers=self.headers, 
                    files=files, 
                    data=data,
                    timeout=30
                )
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            raise StravaAPIError(f"Upload failed: {e}")

    def poll_upload_status(
        self, 
        upload_id: int, 
        timeout: int = 60, 
        interval: int = 5
    ) -> dict:
        """
        Poll Strava upload status until processing completes.
        
        Args:
            upload_id: Upload ID returned from upload_gpx
            timeout: Maximum time to wait in seconds
            interval: Time between polls in seconds
            
        Returns:
            dict: Upload status from Strava API
            
        Raises:
            TimeoutError: If processing doesn't complete within timeout
            StravaAPIError: If API request fails
        """
        url = STRAVA_UPLOAD_STATUS_URL.format(upload_id=upload_id)
        deadline = time.time() + timeout
        backoff = interval

        while time.time() < deadline:
            try:
                response = requests.get(url, headers=self.headers, timeout=10)

                # Handle rate limiting with exponential backoff
                if response.status_code == 429:
                    logger.warning(f"Rate limited (429). Waiting {backoff}s before retry...")
                    time.sleep(backoff)
                    backoff = min(backoff * 1.5, 30)  # Max 30s backoff
                    continue

                response.raise_for_status()
                status_data = response.json()

                # Check if processing is complete
                if status_data.get("activity_id") is not None:
                    return status_data

                # Check for error status
                status_msg = status_data.get("status", "")
                if status_msg and "still being processed" not in status_msg:
                    return status_data

                logger.debug(f"Upload still processing... ({status_msg})")
                time.sleep(interval)

            except requests.exceptions.RequestException as e:
                if "429" not in str(e):
                    raise StravaAPIError(f"Status check failed: {e}")

        raise TimeoutError(f"Upload status did not complete within {timeout} seconds.")

    def get_activity(self, activity_id: int) -> Optional[dict]:
        """
        Retrieve a Strava activity by ID.
        
        Args:
            activity_id: Strava activity ID
            
        Returns:
            dict: Activity data, or None if not found
        """
        url = STRAVA_ACTIVITY_URL.format(activity_id=activity_id)
        
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            
            if response.status_code == 404:
                return None
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.Timeout:
            logger.debug(f"Timeout while checking activity {activity_id}")
            return None
        except requests.exceptions.RequestException as e:
            logger.debug(f"Error checking activity {activity_id}: {e}")
            return None

    def wait_for_activity(
        self, 
        activity_id: int, 
        timeout: int = 120, 
        initial_interval: int = 2
    ) -> bool:
        """
        Wait for an activity to become available in the Strava API.
        Uses exponential backoff to reduce API calls.
        
        Args:
            activity_id: Strava activity ID
            timeout: Maximum time to wait in seconds
            initial_interval: Initial time between checks in seconds
            
        Returns:
            bool: True if activity exists, False if timeout reached
        """
        deadline = time.time() + timeout
        attempt = 0
        current_interval = initial_interval
        
        while time.time() < deadline:
            attempt += 1
            activity = self.get_activity(activity_id)
            
            if activity is not None:
                logger.info(f"Activity {activity_id} available after {attempt} attempts")
                return True
            
            remaining = int(deadline - time.time())
            logger.debug(f"Waiting for activity {activity_id}... (attempt {attempt}, {remaining}s remaining)")
            
            time.sleep(current_interval)
            # Exponential backoff: 2s -> 4s -> 8s -> 10s (max)
            current_interval = min(current_interval * 2, 10)
        
        return False

    def update_activity(
        self,
        activity_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        gear_id: Optional[str] = None,
    ) -> dict:
        """
        Update a Strava activity's metadata.
        
        Args:
            activity_id: Strava activity ID
            name: New activity name
            description: New activity description
            gear_id: Gear ID (None to clear gear)
            
        Returns:
            dict: Updated activity data
            
        Raises:
            StravaAPIError: If API request fails
        """
        url = STRAVA_ACTIVITY_URL.format(activity_id=activity_id)

        data = {}
        if name is not None:
            data["name"] = name
        if description is not None:
            data["description"] = description
        if gear_id is None:
            # Explicitly clear gear to prevent default assignment
            data["gear_id"] = None
        else:
            data["gear_id"] = gear_id

        # Only send request if there's data to update
        if not data:
            return {}

        try:
            response = requests.put(url, headers=self.headers, json=data, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise StravaAPIError(f"Update failed: {e}")

    def update_activity_with_retry(
        self,
        activity_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        gear_id: Optional[str] = None,
        max_retries: int = 3,
    ) -> dict:
        """
        Update a Strava activity with retries to handle delayed processing.
        
        Args:
            activity_id: Strava activity ID
            name: New activity name
            description: New activity description
            gear_id: Gear ID (None to clear gear)
            max_retries: Maximum number of retry attempts
            
        Returns:
            dict: Updated activity data
            
        Raises:
            StravaAPIError: If all retries fail
        """
        wait_times = [3, 5, 10]  # Progressive wait times

        for attempt in range(max_retries):
            try:
                return self.update_activity(activity_id, name, description, gear_id)
            except StravaAPIError as ex:
                if attempt < max_retries - 1:
                    wait_time = wait_times[attempt]
                    logger.warning(f"Retry {attempt + 1}/{max_retries} after {wait_time}s: {ex}")
                    time.sleep(wait_time)
                else:
                    raise


def parse_duplicate_activity_id(error_text: Optional[str]) -> Optional[int]:
    """
    Parse the existing activity ID from a duplicate upload error message.
    
    Args:
        error_text: Error message from Strava API
        
    Returns:
        int: Existing activity ID, or None if not found
    """
    if not error_text:
        return None

    match = re.search(r"/activities/(\d+)", error_text)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def strava_activity_type(runtastic_type_id: Optional[str]) -> str:
    """
    Map a Runtastic sport_type_id to a Strava activity type.
    
    Args:
        runtastic_type_id: Runtastic sport type ID
        
    Returns:
        str: Corresponding Strava activity type (defaults to "Run")
    """
    if runtastic_type_id is None:
        return "Run"

    try:
        type_id = int(runtastic_type_id)
        return RUNTASTIC_TO_STRAVA_TYPE.get(type_id, "Run")
    except (TypeError, ValueError):
        return "Run"


def infer_activity_type_from_json(
    gpx_path: Path, json_dir: Optional[Path] = None
) -> Tuple[Optional[str], Optional[Path]]:
    """
    Infer the Strava activity type from a matching Runtastic JSON file.
    
    The function looks for JSON files in the following order:
    1. In json_dir if provided
    2. In parent directory of gpx_path
    3. Next to the gpx_path
    
    Args:
        gpx_path: Path to the GPX file
        json_dir: Optional directory containing JSON metadata files
        
    Returns:
        tuple: (activity_type, json_path) or (None, None) if not found
    """
    # Build list of candidate paths to check
    candidates = []

    if json_dir is not None:
        candidates.append(json_dir / f"{gpx_path.stem}.json")
    else:
        candidates.append(gpx_path.parent.parent / f"{gpx_path.stem}.json")

    candidates.extend([
        gpx_path.with_suffix(".json"),
        gpx_path.parent / f"{gpx_path.stem}.json",
    ])

    # Try each candidate path
    for json_path in candidates:
        if not json_path.exists():
            continue

        try:
            with json_path.open("r", encoding="utf-8") as json_file:
                data = json.load(json_file)

            activity_type = strava_activity_type(data.get("sport_type_id"))
            return activity_type, json_path

        except (json.JSONDecodeError, IOError) as e:
            logger.debug(f"Could not read {json_path}: {e}")
            continue

    return None, None


def find_gpx_files(directory: Path, year: Optional[int] = None) -> list[Path]:
    """
    Find all GPX files in a directory, optionally filtered by year.
    
    Args:
        directory: Directory to search
        year: Optional year filter (checks if year appears in filename)
        
    Returns:
        list: Sorted list of GPX file paths
    """
    gpx_files = sorted(directory.glob("*.gpx"))

    if year is None:
        return gpx_files

    year_str = str(year)
    return [gpx_file for gpx_file in gpx_files if year_str in gpx_file.name]


def upload_single_file(
    uploader: StravaUploader,
    gpx_path: Path,
    activity_type: Optional[str] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    private: bool = True,
    trainer: bool = False,
    commute: bool = False,
    timeout: int = 60,
    json_dir: Optional[Path] = None,
    skip_metadata_update: bool = False,
    wait_timeout: int = 120,
) -> bool:
    """
    Upload a single GPX file to Strava.
    
    Args:
        uploader: StravaUploader instance
        gpx_path: Path to the GPX file
        activity_type: Type of activity (inferred if None)
        name: Activity name
        description: Activity description
        private: Whether the activity should be private
        trainer: Whether this is a trainer activity
        commute: Whether this is a commute
        timeout: Upload status polling timeout
        json_dir: Directory containing JSON metadata files
        skip_metadata_update: Skip metadata update after upload
        wait_timeout: Seconds to wait for activity availability
        
    Returns:
        bool: True if upload succeeded, False otherwise
    """
    logger.info(f"Uploading {gpx_path.name}...")

    # Infer activity type from JSON metadata if not provided
    inferred_activity_type, json_path = infer_activity_type_from_json(
        gpx_path, json_dir=json_dir
    )
    selected_activity_type = activity_type or inferred_activity_type or "Run"

    if activity_type is None and inferred_activity_type is not None:
        logger.info(f"Inferred activity type '{selected_activity_type}' from {json_path.name}")

    try:
        # Upload the GPX file
        result = uploader.upload_gpx(
            gpx_path=gpx_path,
            activity_type=selected_activity_type,
            name=name,
            description=description,
            private=private,
            trainer=trainer,
            commute=commute,
        )
        logger.debug(f"Upload response: {json.dumps(result, indent=2)}")

        upload_id = result.get("id")
        if upload_id is None:
            logger.error(f"No upload ID returned for {gpx_path.name}")
            return False

        # Poll for upload completion
        logger.info(f"Upload submitted, ID: {upload_id}")
        status = uploader.poll_upload_status(upload_id, timeout=timeout)
        logger.debug(f"Upload status: {json.dumps(status, indent=2)}")

        # Get activity ID
        activity_id = status.get("activity_id")

        # Handle duplicate uploads
        if activity_id is None or activity_id == 0:
            duplicate_id = parse_duplicate_activity_id(status.get("error"))
            if duplicate_id is not None:
                activity_id = duplicate_id
                logger.warning(f"Duplicate detected, existing activity ID: {activity_id}")
            else:
                logger.error(f"No valid activity ID returned for {gpx_path.name}")
                return False

        # Skip metadata update if requested
        if skip_metadata_update:
            logger.info(f"✓ Uploaded {gpx_path.name} (Activity ID: {activity_id})")
            logger.info("  Metadata update skipped as requested")
            return True
            
        # Wait for activity to be available
        logger.info(f"Waiting for activity {activity_id} to become available...")
        if not uploader.wait_for_activity(activity_id, timeout=wait_timeout):
            logger.warning(f"⚠ Activity {activity_id} did not become available within {wait_timeout}s")
            logger.warning("  The activity was created but gear will NOT be cleared")
            logger.warning("  You may need to manually remove the default gear on Strava")
            logger.info(f"✓ Uploaded {gpx_path.name} (Activity ID: {activity_id})")
            return True

        # Update activity metadata to clear gear
        logger.info(f"Activity {activity_id} is ready. Clearing default gear...")
        time.sleep(2)  # Brief pause before updating

        try:
            updated = uploader.update_activity_with_retry(
                activity_id=activity_id,
                name=name,
                description=description,
                gear_id=None,  # This is the important part - clears default gear
            )
            logger.debug(f"Updated activity: {json.dumps(updated, indent=2)}")
            logger.info(f"✓ Successfully uploaded {gpx_path.name} (Activity ID: {activity_id})")
            logger.info("  Default gear cleared successfully")
            return True

        except StravaAPIError as update_exc:
            logger.warning(f"⚠ Failed to clear gear: {update_exc}")
            logger.info(f"✓ Uploaded {gpx_path.name} but gear may still be assigned (Activity ID: {activity_id})")
            return True

    except Exception as exc:
        logger.error(f"✗ Failed to upload {gpx_path.name}: {exc}")
        return False


def upload_directory(
    uploader: StravaUploader,
    directory: Path,
    activity_type: Optional[str] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    private: bool = True,
    trainer: bool = False,
    commute: bool = False,
    timeout: int = 60,
    year: Optional[int] = None,
    json_dir: Optional[Path] = None,
    skip_metadata_update: bool = False,
    wait_timeout: int = 120,
) -> Tuple[int, int]:
    """
    Upload all GPX files in a directory to Strava.
    
    Args:
        uploader: StravaUploader instance
        directory: Directory containing GPX files
        activity_type: Type of activity (inferred if None)
        name: Activity name
        description: Activity description
        private: Whether activities should be private
        trainer: Whether these are trainer activities
        commute: Whether these are commutes
        timeout: Upload status polling timeout
        year: Optional year filter for filenames
        json_dir: Directory containing JSON metadata files
        skip_metadata_update: Skip metadata update after upload
        wait_timeout: Seconds to wait for activity availability
        
    Returns:
        tuple: (success_count, failure_count)
        
    Raises:
        FileNotFoundError: If directory doesn't exist or contains no GPX files
        NotADirectoryError: If path is not a directory
    """
    if not directory.exists():
        raise FileNotFoundError(f"Directory not found: {directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")

    gpx_files = find_gpx_files(directory, year=year)
    if not gpx_files:
        year_msg = f" for year {year}" if year else ""
        raise FileNotFoundError(f"No GPX files found in {directory}{year_msg}")

    logger.info(f"Found {len(gpx_files)} GPX file(s) to upload")

    # Upload each file
    success_count = 0
    failure_count = 0

    for i, gpx_file in enumerate(gpx_files, 1):
        logger.info(f"\n[{i}/{len(gpx_files)}] Processing {gpx_file.name}")
        
        success = upload_single_file(
            uploader=uploader,
            gpx_path=gpx_file,
            activity_type=activity_type,
            name=name,
            description=description,
            private=private,
            trainer=trainer,
            commute=commute,
            timeout=timeout,
            json_dir=json_dir,
            skip_metadata_update=skip_metadata_update,
            wait_timeout=wait_timeout,
        )
        
        if success:
            success_count += 1
        else:
            failure_count += 1

    return success_count, failure_count


def main() -> int:
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Upload GPX files to Strava with automatic activity type detection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Upload a single GPX file
  %(prog)s /path/to/file.gpx --access-token YOUR_TOKEN
  
  # Upload all GPX files in a directory
  %(prog)s /path/to/directory --access-token YOUR_TOKEN
  
  # Upload only 2024 activities with metadata inference
  %(prog)s ./Sport-sessions/GPS-data --year 2024 --json-dir ./Sport-sessions
  
  # Upload with custom name and skip gear update (faster)
  %(prog)s file.gpx --name "Morning Run" --skip-metadata-update
  
  # Increase wait timeout for slow API responses
  %(prog)s ./Sport-sessions/GPS-data --wait-timeout 180
        """,
    )

    parser.add_argument(
        "gpx_path",
        type=str,
        nargs="?",
        default="./Sport-sessions/GPS-data",
        help="Path to GPX file or directory (default: ./Sport-sessions/GPS-data)",
    )
    parser.add_argument(
        "--access-token",
        type=str,
        default=os.environ.get("STRAVA_ACCESS_TOKEN"),
        help="Strava access token (or set STRAVA_ACCESS_TOKEN env var)",
    )
    parser.add_argument(
        "--activity-type",
        type=str,
        default=None,
        help="Strava activity type (e.g., Run, Ride, Walk). Auto-inferred if omitted.",
    )
    parser.add_argument(
        "--json-dir",
        type=str,
        default=None,
        help="Directory containing Runtastic JSON metadata for activity type inference",
    )
    parser.add_argument(
        "--name",
        type=str,
        default=None,
        help="Activity name (auto-generated if omitted)",
    )
    parser.add_argument(
        "--description",
        type=str,
        default=None,
        help="Activity description (auto-generated if omitted)",
    )
    parser.add_argument(
        "--private",
        action="store_true",
        default=True,
        help="Make activities private (default: True)",
    )
    parser.add_argument(
        "--trainer",
        action="store_true",
        help="Mark activities as trainer activities",
    )
    parser.add_argument(
        "--commute",
        action="store_true",
        help="Mark activities as commutes",
    )
    parser.add_argument(
        "--poll-timeout",
        type=int,
        default=60,
        help="Seconds to wait for Strava upload processing (default: 60)",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Upload only GPX files containing this year in the filename",
    )
    parser.add_argument(
        "--skip-metadata-update",
        action="store_true",
        help="Skip metadata update after upload (faster but won't clear default gear)",
    )
    parser.add_argument(
        "--wait-timeout",
        type=int,
        default=120,
        help="Seconds to wait for activity to become available (default: 120)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging (debug mode)",
    )

    args = parser.parse_args()

    # Validate access token
    if not args.access_token:
        parser.error(
            "Missing access token. Provide --access-token or set STRAVA_ACCESS_TOKEN environment variable."
        )

    # Expand and validate path
    gpx_path = Path(os.path.expanduser(args.gpx_path))
    if not gpx_path.exists():
        parser.error(
            f"Path not found: {args.gpx_path}\n"
            "Use an absolute path or ensure the file/directory exists."
        )

    # Parse json_dir if provided
    json_dir = Path(os.path.expanduser(args.json_dir)) if args.json_dir else None

    # Initialize uploader
    uploader = StravaUploader(args.access_token, verbose=args.verbose)

    try:
        # Upload directory or single file
        if gpx_path.is_dir():
            success_count, failure_count = upload_directory(
                uploader=uploader,
                directory=gpx_path,
                activity_type=args.activity_type,
                name=args.name,
                description=args.description,
                private=args.private,
                trainer=args.trainer,
                commute=args.commute,
                timeout=args.poll_timeout,
                year=args.year,
                json_dir=json_dir,
                skip_metadata_update=args.skip_metadata_update,
                wait_timeout=args.wait_timeout,
            )
            
            # Summary
            total = success_count + failure_count
            logger.info(f"\n{'='*60}")
            logger.info(f"Upload Summary:")
            logger.info(f"  Successful: {success_count}/{total}")
            logger.info(f"  Failed: {failure_count}/{total}")
            logger.info(f"{'='*60}")
            
            return 0 if failure_count == 0 else 1
            
        else:
            success = upload_single_file(
                uploader=uploader,
                gpx_path=gpx_path,
                activity_type=args.activity_type,
                name=args.name,
                description=args.description,
                private=args.private,
                trainer=args.trainer,
                commute=args.commute,
                timeout=args.poll_timeout,
                json_dir=json_dir,
                skip_metadata_update=args.skip_metadata_update,
                wait_timeout=args.wait_timeout,
            )
            
            return 0 if success else 1
            
    except KeyboardInterrupt:
        logger.warning("\n\nUpload interrupted by user")
        return 130
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
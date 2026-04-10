import argparse
import json
import os
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


def upload_gpx(
    access_token: str,
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
        access_token: Strava OAuth access token
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
        requests.HTTPError: If API request fails
    """
    if not gpx_path.exists():
        raise FileNotFoundError(f"GPX file not found: {gpx_path}")

    headers = {"Authorization": f"Bearer {access_token}"}

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
        "gear_id": "",  # Empty string to prevent default gear assignment (but it doesnt' work)
    }

    if description:
        data["description"] = description

    with gpx_path.open("rb") as gpx_file:
        files = {"file": (gpx_path.name, gpx_file, "application/gpx+xml")}
        response = requests.post(
            STRAVA_UPLOAD_URL, headers=headers, files=files, data=data
        )

    response.raise_for_status()
    return response.json()


def poll_upload_status(
    access_token: str, upload_id: int, timeout: int = 60, interval: int = 5
) -> dict:
    """
    Poll Strava upload status until processing completes.
    
    Args:
        access_token: Strava OAuth access token
        upload_id: Upload ID returned from upload_gpx
        timeout: Maximum time to wait in seconds
        interval: Time between polls in seconds
        
    Returns:
        dict: Upload status from Strava API
        
    Raises:
        TimeoutError: If processing doesn't complete within timeout
        requests.HTTPError: If API request fails
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    url = STRAVA_UPLOAD_STATUS_URL.format(upload_id=upload_id)
    deadline = time.time() + timeout
    backoff = interval

    while time.time() < deadline:
        try:
            response = requests.get(url, headers=headers)

            # Handle rate limiting with exponential backoff
            if response.status_code == 429:
                print(f"Rate limited (429). Waiting {backoff}s before retry...")
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

            time.sleep(interval)

        except requests.exceptions.RequestException as e:
            if "429" not in str(e):
                raise

    raise TimeoutError(f"Upload status did not complete within {timeout} seconds.")


def get_activity(access_token: str, activity_id: int) -> Optional[dict]:
    """
    Retrieve a Strava activity by ID.
    
    Args:
        access_token: Strava OAuth access token
        activity_id: Strava activity ID
        
    Returns:
        dict: Activity data, or None if not found
        
    Raises:
        requests.HTTPError: If API request fails (except 404)
    """
    url = STRAVA_ACTIVITY_URL.format(activity_id=activity_id)
    headers = {"Authorization": f"Bearer {access_token}"}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 404:
            return None
        
        response.raise_for_status()
        return response.json()
        
    except requests.exceptions.Timeout:
        print(f"  Timeout while checking activity {activity_id}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"  Error checking activity {activity_id}: {e}")
        return None


def wait_for_activity_existence(
    access_token: str, activity_id: int, timeout: int = 60, interval: int = 5
) -> bool:
    """
    Wait for an activity to become available in the Strava API.
    
    Args:
        access_token: Strava OAuth access token
        activity_id: Strava activity ID
        timeout: Maximum time to wait in seconds
        interval: Time between checks in seconds
        
    Returns:
        bool: True if activity exists, False if timeout reached
    """
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        activity = get_activity(access_token, activity_id)
        if activity is not None:
            print(f"Activity {activity_id} is now available (after {attempt} attempts)")
            return True
        print(f"Waiting for activity {activity_id} to be available... (attempt {attempt})")
        time.sleep(interval)
    return False


def update_activity(
    access_token: str,
    activity_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    gear_id: Optional[str] = None,
) -> dict:
    """
    Update a Strava activity's metadata.
    
    Args:
        access_token: Strava OAuth access token
        activity_id: Strava activity ID
        name: New activity name
        description: New activity description
        gear_id: Gear ID (None to clear gear)
        
    Returns:
        dict: Updated activity data
        
    Raises:
        requests.HTTPError: If API request fails
    """
    url = STRAVA_ACTIVITY_URL.format(activity_id=activity_id)
    headers = {"Authorization": f"Bearer {access_token}"}

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

    response = requests.put(url, headers=headers, json=data)
    response.raise_for_status()
    return response.json()


def update_activity_with_retry(
    access_token: str,
    activity_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    gear_id: Optional[str] = None,
    max_retries: int = 3,
) -> dict:
    """
    Update a Strava activity with retries to handle delayed processing.
    
    Args:
        access_token: Strava OAuth access token
        activity_id: Strava activity ID
        name: New activity name
        description: New activity description
        gear_id: Gear ID (None to clear gear)
        max_retries: Maximum number of retry attempts
        
    Returns:
        dict: Updated activity data
        
    Raises:
        Exception: If all retries fail
    """
    wait_times = [3, 5, 10]  # Progressive wait times

    for attempt in range(max_retries):
        try:
            return update_activity(access_token, activity_id, name, description, gear_id)
        except Exception as ex:
            if attempt < max_retries - 1:
                wait_time = wait_times[attempt]
                print(f"Retry {attempt + 1}/{max_retries} after {wait_time}s: {ex}")
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

    import re
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
            print(f"Warning: Could not read {json_path}: {e}")
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
    access_token: str,
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
) -> None:
    """
    Upload a single GPX file to Strava.
    
    Args:
        access_token: Strava OAuth access token
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
    """
    print(f"\nUploading {gpx_path.name}...")

    # Infer activity type from JSON metadata if not provided
    inferred_activity_type, json_path = infer_activity_type_from_json(
        gpx_path, json_dir=json_dir
    )
    selected_activity_type = activity_type or inferred_activity_type or "Run"

    if activity_type is None and inferred_activity_type is not None:
        print(f"Inferred activity type '{selected_activity_type}' from {json_path}")

    try:
        # Upload the GPX file
        result = upload_gpx(
            access_token=access_token,
            gpx_path=gpx_path,
            activity_type=selected_activity_type,
            name=name,
            description=description,
            private=private,
            trainer=trainer,
            commute=commute,
        )
        print(f"Upload response: {json.dumps(result, indent=2, ensure_ascii=False)}")

        upload_id = result.get("id")
        if upload_id is None:
            print(f"Warning: No upload ID returned for {gpx_path.name}")
            return

        # Poll for upload completion
        print(f"Upload submitted, upload ID: {upload_id}")
        status = poll_upload_status(access_token, upload_id, timeout=timeout)
        print(f"Upload status: {json.dumps(status, indent=2, ensure_ascii=False)}")

        # Get activity ID
        activity_id = status.get("activity_id")

        # Handle duplicate uploads
        if activity_id is None or activity_id == 0:
            duplicate_id = parse_duplicate_activity_id(status.get("error"))
            if duplicate_id is not None:
                activity_id = duplicate_id
                print(f"Duplicate detected, existing activity ID: {activity_id}")
            else:
                print(f"Warning: No valid activity ID returned for {gpx_path.name}")
                return

        # Wait for activity to be available
        if skip_metadata_update:
            print(f"✓ Successfully uploaded {gpx_path.name} (Activity ID: {activity_id})")
            print(f"  Metadata update skipped as requested.")
            return
            
        print(f"Waiting for activity {activity_id} to become available...")
        if not wait_for_activity_existence(access_token, activity_id, timeout=60):
            print(f"⚠ Warning: Activity {activity_id} did not become available within 60s.")
            print(f"  The activity was created but metadata update will be skipped.")
            print(f"  You can manually update the activity on Strava if needed.")
            print(f"✓ Uploaded {gpx_path.name} (Activity ID: {activity_id})")
            return

        # Update activity metadata
        print(f"Activity {activity_id} is ready. Updating metadata...")
        time.sleep(5)  # Brief pause before updating

        try:
            updated = update_activity_with_retry(
                access_token=access_token,
                activity_id=activity_id,
                name=name,
                description=description,
                gear_id=None,
            )
            print(f"Updated activity: {json.dumps(updated, indent=2, ensure_ascii=False)}")
            print(f"✓ Successfully uploaded {gpx_path.name} (Activity ID: {activity_id})")

        except Exception as update_exc:
            print(f"Warning: Failed to update activity metadata: {update_exc}")
            print(f"✓ Uploaded {gpx_path.name} but metadata update failed (Activity ID: {activity_id})")

    except Exception as exc:
        print(f"✗ Failed to upload {gpx_path.name}: {exc}")


def upload_directory(
    access_token: str,
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
) -> None:
    """
    Upload all GPX files in a directory to Strava.
    
    Args:
        access_token: Strava OAuth access token
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

    print(f"Found {len(gpx_files)} GPX file(s) to upload")

    # Upload each file
    success_count = 0
    failure_count = 0

    for gpx_file in gpx_files:
        try:
            upload_single_file(
                access_token=access_token,
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
            )
            success_count += 1
        except Exception as e:
            print(f"Error uploading {gpx_file.name}: {e}")
            failure_count += 1

    # Summary
    print(f"\n{'='*60}")
    print(f"Upload Summary:")
    print(f"  Successful: {success_count}/{len(gpx_files)}")
    print(f"  Failed: {failure_count}/{len(gpx_files)}")
    print(f"{'='*60}")


def main() -> None:
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Upload GPX files to Strava",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Upload a single GPX file
  python strava_upload.py /path/to/file.gpx --access-token YOUR_TOKEN
  
  # Upload all GPX files in a directory
  python strava_upload.py /path/to/directory --access-token YOUR_TOKEN
  
  # Upload only 2024 activities with metadata inference
  python strava_upload.py ./Sport-sessions/GPS-data --year 2024 --json-dir ./Sport-sessions
  
  # Upload with custom name and description
  python strava_upload.py file.gpx --name "Morning Run" --description "Great weather!"
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

    # Upload directory or single file
    if gpx_path.is_dir():
        upload_directory(
            access_token=args.access_token,
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
        )
    else:
        upload_single_file(
            access_token=args.access_token,
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
        )


if __name__ == "__main__":
    main()
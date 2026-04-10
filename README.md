# Strava GPX Uploader

Utiliy tool to upload GPX files from Adidas Running (ex Runtastic) to Strava with automatic activity type detection. 

## Features

- 🚀 **Batch upload** - Upload entire directories of GPX files
- 🔍 **Auto-detection** - Automatically infers activity type from Runtastic JSON metadata
- 🔄 **Smart retry** - Exponential backoff and automatic retry on failures
- 📊 **Progress tracking** - Real-time progress with detailed logging
- 🎯 **Year filtering** - Upload only specific years
- ⚙️ **Configurable** - Extensive options for customization

## Requirements

- Python 3.9+
- `requests` library

## Installation

1. Clone or download this repository
2. Install dependencies:

```bash
pip install requests
```

3. Make the script executable (optional):

```bash
chmod +x strava_upload_final.py
```

## Getting a Strava Access Token

1. Go to https://www.strava.com/settings/api
2. Create an application (if you haven't already)
3. Note your **Client ID** and **Client Secret**
4. Use the following URL to authorize (replace `YOUR_CLIENT_ID`):

```
https://www.strava.com/oauth/authorize?client_id=YOUR_CLIENT_ID&redirect_uri=http://localhost&response_type=code&scope=activity:write
```

5. Authorize the app and copy the `code` from the redirect URL
6. Exchange the code for an access token:

```bash
curl -X POST https://www.strava.com/oauth/token \
  -d client_id=YOUR_CLIENT_ID \
  -d client_secret=YOUR_CLIENT_SECRET \
  -d code=AUTHORIZATION_CODE \
  -d grant_type=authorization_code
```

7. Use the `access_token` from the response

## Usage

### Basic Usage

```bash
# Upload a single GPX file
python3 strava_upload_final.py /path/to/file.gpx --access-token YOUR_TOKEN

# Upload all GPX files in a directory
python3 strava_upload_final.py /path/to/directory --access-token YOUR_TOKEN
```

### Upload with Year Filter

```bash
# Upload only 2015 activities
python3 strava_upload_final.py ./Sport-sessions/GPS-data --year 2015 --access-token YOUR_TOKEN
```

### Upload with Activity Type Inference

```bash
# Automatically detect activity type from Runtastic JSON files
python3 strava_upload_final.py ./Sport-sessions/GPS-data \
  --year 2015 \
  --json-dir ./Sport-sessions \
  --access-token YOUR_TOKEN
```

### Upload with Custom Settings

```bash
# Custom name, description, and skip gear update
python3 strava_upload_final.py file.gpx \
  --name "Morning Run" \
  --description "Great weather!" \
  --skip-metadata-update \
  --access-token YOUR_TOKEN
```

### Debug Mode

```bash
# Enable verbose logging to see all API responses
python3 strava_upload_final.py ./Sport-sessions/GPS-data \
  --year 2015 \
  --access-token YOUR_TOKEN \
  --verbose
```

### Using Environment Variable

```bash
# Set the access token as an environment variable
export STRAVA_ACCESS_TOKEN=your_token_here

# Now you don't need to specify --access-token
python3 strava_upload_final.py ./Sport-sessions/GPS-data --year 2015
```

## Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `gpx_path` | Path to GPX file or directory | `./Sport-sessions/GPS-data` |
| `--access-token` | Strava access token | `$STRAVA_ACCESS_TOKEN` |
| `--activity-type` | Activity type (Run, Ride, Walk, etc.) | Auto-inferred |
| `--json-dir` | Directory with Runtastic JSON metadata | `None` |
| `--name` | Activity name | Auto-generated |
| `--description` | Activity description | Auto-generated |
| `--private` | Make activities private | `True` |
| `--trainer` | Mark as trainer activity | `False` |
| `--commute` | Mark as commute | `False` |
| `--poll-timeout` | Upload processing timeout (seconds) | `60` |
| `--year` | Upload only files with this year | `None` |
| `--skip-metadata-update` | Skip gear clearing (faster) | `False` |
| `--wait-timeout` | Wait for activity availability (seconds) | `120` |
| `--verbose`, `-v` | Enable debug logging | `False` |

## Activity Type Mapping

The script automatically maps Runtastic activity types to Strava:

| Runtastic ID | Strava Type |
|--------------|-------------|
| 1 | Run |
| 2 | Walk |
| 3, 4, 15, 22 | Ride |
| 7, 19 | Walk |
| 13 | Hike |
| 18 | Swim |
| 82 | Trail Run |

## How It Works

### Upload Process

1. **Upload GPX** - File is uploaded to Strava
2. **Poll Status** - Wait for Strava to process the upload
3. **Get Activity ID** - Extract the created activity ID
4. **Wait for Availability** - Wait for activity to be available in API (with exponential backoff)
5. **Clear Gear** - Update activity to remove default gear assignment

### Strava defaults (important!)

When you upload a running activity, Strava automatically assigns your default running shoes and privacy visibility you set for your profile. For imported historical activities, you might prefer differently. Even though the script clears this automatic assignment of gears by setting `gear_id=None`, Strava overwrites it anyway, same for privacy. I suggest to set temporary the preferences you need in Strava settings and go back as you're done with importing.

### Exponential Backoff

The script uses smart retry logic:
- First check: 2 seconds
- Second check: 4 seconds
- Third check: 8 seconds
- Subsequent: 10 seconds (maximum)

This reduces API calls while ensuring activities become available.

## Error Handling

### Rate Limiting

If you hit Strava's rate limit (429 error), the script automatically:
- Waits with exponential backoff
- Retries the request
- Maximum backoff: 30 seconds

### Duplicate Detection

If you try to upload a file that already exists on Strava:
- The script detects the duplicate
- Extracts the existing activity ID
- Optionally updates metadata on the existing activity

### Timeout Issues

If activities don't become available within the timeout:
- The activity is still created on Strava
- Gear clearing is skipped
- You can manually remove gear later if needed

**Solutions:**
```bash
# Increase timeout to 3 minutes
python3 strava_upload_final.py ./Sport-sessions/GPS-data \
  --wait-timeout 180 \
  --access-token YOUR_TOKEN

# Or skip gear update entirely (faster)
python3 strava_upload_final.py ./Sport-sessions/GPS-data \
  --skip-metadata-update \
  --access-token YOUR_TOKEN
```

## Directory Structure

```
Sport-sessions/
├── GPS-data/
│   ├── 2015-01-01_12-00-00.gpx
│   ├── 2015-01-02_12-00-00.gpx
│   └── ...
├── 2015-01-01_12-00-00.json  # Activity metadata
├── 2015-01-02_12-00-00.json
└── ...
```

The script looks for matching JSON files to infer activity type:
1. In `--json-dir` (if specified)
2. In parent directory of GPX file
3. Next to the GPX file

## Examples

### Example 1: Upload All 2024 Activities

```bash
python3 strava_upload_final.py ./Sport-sessions/GPS-data \
  --year 2024 \
  --json-dir ./Sport-sessions \
  --access-token cc23f3f735ddb4454c9f979ccea953e3071697d3
```

**Output:**
```
2024-01-15 10:30:45 - INFO - Found 45 GPX file(s) to upload

[1/45] Processing 2024-01-01_08-30-00.gpx
2024-01-15 10:30:46 - INFO - Uploading 2024-01-01_08-30-00.gpx...
2024-01-15 10:30:46 - INFO - Inferred activity type 'Run' from 2024-01-01_08-30-00.json
2024-01-15 10:30:47 - INFO - Upload submitted, ID: 19139903659
2024-01-15 10:30:52 - INFO - Waiting for activity 18038693567 to become available...
2024-01-15 10:30:54 - INFO - Activity 18038693567 available after 1 attempts
2024-01-15 10:30:54 - INFO - Activity 18038693567 is ready. Clearing default gear...
2024-01-15 10:30:57 - INFO - ✓ Successfully uploaded 2024-01-01_08-30-00.gpx (Activity ID: 18038693567)
2024-01-15 10:30:57 - INFO -   Default gear cleared successfully

[2/45] Processing 2024-01-02_09-15-00.gpx
...

============================================================
Upload Summary:
  Successful: 45/45
  Failed: 0/45
============================================================
```

### Example 2: Upload Single File with Custom Name

```bash
python3 strava_upload_final.py my-run.gpx \
  --name "Morning 10K" \
  --description "Personal best!" \
  --activity-type Run \
  --access-token YOUR_TOKEN
```

### Example 3: Fast Upload (Skip Gear Update)

```bash
# Upload 500 activities without waiting for gear update
python3 strava_upload_final.py ./Sport-sessions/GPS-data \
  --skip-metadata-update \
  --access-token YOUR_TOKEN
```

### Example 4: Debug Failed Upload

```bash
# Enable verbose mode to see all API responses
python3 strava_upload_final.py problematic-file.gpx \
  --verbose \
  --access-token YOUR_TOKEN
```

## Troubleshooting

### "Activity did not become available within Xs"

**Problem:** Strava's API is slow to make the activity available.

**Solutions:**
1. Increase timeout: `--wait-timeout 300`
2. Skip metadata update: `--skip-metadata-update`
3. Wait and manually remove gear later on Strava

### "Rate limited (429)"

**Problem:** You're making too many API requests.

**Solution:** The script automatically handles this with exponential backoff. Just wait.

### "Missing access token"

**Problem:** No access token provided.

**Solutions:**
1. Use `--access-token YOUR_TOKEN`
2. Set environment variable: `export STRAVA_ACCESS_TOKEN=YOUR_TOKEN`

### "No GPX files found"

**Problem:** Directory is empty or wrong year filter.

**Solutions:**
1. Check the directory path
2. Remove `--year` filter to see all files
3. Ensure files have `.gpx` extension

### "Failed to upload: 401 Unauthorized"

**Problem:** Invalid or expired access token.

**Solution:** Generate a new access token (see "Getting a Strava Access Token" section).

## Performance Tips

1. **Use `--skip-metadata-update`** for bulk uploads if you don't care about adding additional metadata (ex: gear)
2. **Increase `--wait-timeout`** if you frequently see timeout warnings
3. **Use `--year` filter** to upload in batches by year
4. **Run at off-peak hours** to avoid Strava API rate limits
5. **Use environment variable** for access token to avoid typing it repeatedly

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success (all files uploaded) |
| 1 | Error (some or all files failed) |
| 130 | Interrupted by user (Ctrl+C) |

## Privacy & Security

- Even though sctivities are set as **private by default** (`--private` is True), don't forget to check your Strava account default options for visibility that might be overrided.
- Your access token is never logged or stored by the script
- Use environment variables for tokens to avoid command history exposure

## Contributing

The repo has evident limits. Feel free to make the use you want :)

## License

MIT -
This script is provided as-is for personal use. Strava API usage must comply with [Strava's API Agreement](https://www.strava.com/legal/api).

## Credits

Special credits to @maxschaffelder repository: https://github.com/maxschaffelder/runtastic-to-strava.git

---

**Happy running! 🚴‍♂️🏃‍♀️**
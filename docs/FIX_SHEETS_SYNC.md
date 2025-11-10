# Fix: Google Sheets Sync Job Not Working

## Problem
The Cloud Run Job `sync-topics-daily` is failing with:
```
/usr/local/bin/python3: can't open file '/app/scripts/sync_sheets_to_chromadb.py': [Errno 2] No such file or directory
```

## Root Cause
The Cloud Run Job is using an **old Docker image** that doesn't include the `scripts/` folder. The script exists in the repo but wasn't in the image when the job was first created.

## Solution

### Option 1: Automated Script (Recommended)

Run the update script from your local machine:

```bash
cd /path/to/x_bot_mei
bash scripts/update_sheets_sync_job.sh
```

This script will:
1. Get the latest image tag from the Cloud Run Service (x-bot-mei)
2. Update the Cloud Run Job to use the same image
3. Show the updated configuration

### Option 2: Manual Update via gcloud

```bash
# Set variables
PROJECT_ID="xbot-473616"
REGION="europe-west1"

# Get latest image from the bot service
LATEST_IMAGE=$(gcloud run services describe x-bot-mei \
  --region=$REGION \
  --format="value(spec.template.spec.containers[0].image)" \
  --project=$PROJECT_ID)

echo "Latest image: $LATEST_IMAGE"

# Update the job with latest image
gcloud run jobs update sync-topics-daily \
  --image="$LATEST_IMAGE" \
  --region=$REGION \
  --project=$PROJECT_ID
```

### Option 3: Manual Update via Console

1. Go to Cloud Run Jobs:
   ```
   https://console.cloud.google.com/run/jobs/details/europe-west1/sync-topics-daily?project=xbot-473616
   ```

2. Click **"EDIT"**

3. Under **"Container"** section:
   - Find **Container image URL**
   - Replace with the latest image from the bot service
   - Get it from: https://console.cloud.google.com/run/detail/europe-west1/x-bot-mei?project=xbot-473616
   - Look for the image tag (something like `europe-west1-docker.pkg.dev/xbot-473616/x-bot-mei/x-bot-mei:latest` or with a git SHA)

4. Verify **Command** is: `python3`

5. Verify **Arguments** is: `scripts/sync_sheets_to_chromadb.py`

6. Click **"DEPLOY"**

## Verify the Fix

### Test the job manually

```bash
gcloud run jobs execute sync-topics-daily \
  --region=europe-west1 \
  --project=xbot-473616 \
  --wait
```

### Check logs

```bash
gcloud logging read \
  "resource.type=cloud_run_job AND resource.labels.job_name=sync-topics-daily" \
  --limit=50 \
  --format=json \
  --project=xbot-473616
```

Look for:
- ✅ `"Read X topics from Google Sheet"`
- ✅ `"Found X existing topics in ChromaDB"`
- ✅ `"No new topics to ingest"` or `"Sync complete. Ingested X/Y new topics"`

### Verify in Telegram

After the next scheduled run (3 AM Europe/Madrid), check:
```
/pdfs
```

Should show increased count if new topics were added to Google Sheet.

## Prevention

To prevent this from happening again, **always rebuild and deploy the job** when updating scripts:

```bash
# After making changes to scripts/, rebuild and deploy
gcloud builds submit --config deploy/cloudbuild.yaml

# Then update the job (run the script above)
bash scripts/update_sheets_sync_job.sh
```

Or add the job update to your CI/CD pipeline.

## Alternative: Verify Docker Image Contains Scripts

You can verify the current image has the scripts:

```bash
# Get the current image
IMAGE=$(gcloud run jobs describe sync-topics-daily \
  --region=europe-west1 \
  --format="value(spec.template.spec.containers[0].image)" \
  --project=xbot-473616)

# Pull and inspect (requires Docker Desktop or similar)
docker pull "$IMAGE"
docker run --rm "$IMAGE" ls -la /app/scripts/
```

Should show:
```
sync_sheets_to_chromadb.py
test_sheets_sync.py
... (other scripts)
```

If `sync_sheets_to_chromadb.py` is missing, the image needs to be rebuilt.

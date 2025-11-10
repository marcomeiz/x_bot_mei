# Analytics & Access Control

## 📊 Analytics Endpoint

Track bot usage, errors, and performance metrics.

### Accessing Analytics

**Endpoint:** `GET /analytics?token=YOUR_ADMIN_API_TOKEN`

**Example:**
```bash
curl "https://x-bot-mei-xxxxx.run.app/analytics?token=YOUR_TOKEN"
```

### Response Format

```json
{
  "ok": true,
  "analytics": {
    "overview": {
      "uptime": "3d 14h 23m",
      "total_generations": 1523,
      "total_comments": 342,
      "total_errors": 4,
      "avg_response_time_seconds": 23.5
    },
    "today": {
      "generations": 87,
      "comments": 23,
      "errors": 1,
      "unique_users": 3
    },
    "last_7_days": {
      "generations": 456,
      "comments": 98,
      "errors": 5,
      "unique_users": 5
    },
    "models": {
      "google/gemini-2.0-flash-exp": 1100,
      "deepseek/deepseek-chat-v3.1": 250,
      "anthropic/claude-opus-4.1": 150,
      "openai/gpt-4o": 23
    },
    "commands": {
      "/g": 1100,
      "/g1": 250,
      "/c": 342,
      "/help": 45
    },
    "top_users": [
      {"user_id": "123456", "requests": 450},
      {"user_id": "789012", "requests": 320}
    ],
    "recent_errors": [
      {
        "timestamp": "2025-01-15T14:23:45",
        "user_id": "123456",
        "error_type": "TimeoutError",
        "command": "generation"
      }
    ]
  }
}
```

### Metrics Tracked

- **Total generations/comments**: Lifetime counters
- **Response times**: Average response time (last 100 requests)
- **Model usage**: Which models are most used
- **Command usage**: Which commands are most popular
- **Errors**: Recent errors with context
- **Daily/weekly stats**: Activity trends

---

## 🔒 Private Bot Access (Whitelist)

Restrict bot access to specific users only.

### Configuration

Add to your environment variables (`.env` or Cloud Run):

```bash
# Comma-separated list of allowed Telegram user IDs
ALLOWED_USER_IDS=123456789,987654321,555666777
```

### How It Works

1. **If `ALLOWED_USER_IDS` is empty** → Bot is PUBLIC (anyone can use it)
2. **If `ALLOWED_USER_IDS` has values** → Bot is PRIVATE (only listed users)

### Finding Your User ID

1. Send any message to your bot
2. Bot will respond with access denied
3. Message includes your User ID
4. Add your User ID to `ALLOWED_USER_IDS`
5. Redeploy

**Example access denied message:**
```
🔒 Acceso Denegado

Este bot es privado y solo puede ser usado por usuarios autorizados.

Tu User ID: 123456789

Si crees que deberías tener acceso, contacta al administrador.
```

### Setting Up Private Access

**Step 1:** Get your Telegram User ID

Send `/start` to your bot (before configuring whitelist) or use [@userinfobot](https://t.me/userinfobot).

**Step 2:** Configure environment variable

On Cloud Run:
```bash
gcloud run services update x-bot-mei \
  --region=europe-west1 \
  --project=xbot-473616 \
  --set-env-vars="ALLOWED_USER_IDS=YOUR_USER_ID_HERE"
```

Or in your `.env` file:
```bash
ALLOWED_USER_IDS=123456789
```

**Step 3:** Redeploy and test

After deploy, only users in the whitelist can use the bot.

### Multiple Users

Separate multiple user IDs with commas (no spaces):
```bash
ALLOWED_USER_IDS=123456789,987654321,555666777
```

### Monitoring Access Attempts

Check logs for unauthorized access attempts:
```bash
gcloud logging read 'resource.type="cloud_run_revision"
  AND textPayload:"Access denied"' \
  --project=xbot-473616 \
  --limit=20
```

---

## 📈 Usage Dashboard

Create a simple dashboard to visualize analytics:

**Option 1: Command Line (jq)**
```bash
curl -s "https://your-bot.run.app/analytics?token=TOKEN" | jq '.analytics.overview'
```

**Option 2: Browser**

Visit: `https://your-bot.run.app/analytics?token=YOUR_TOKEN`

**Option 3: Monitoring Tool**

Connect to Google Cloud Monitoring or use tools like Grafana to visualize the JSON data.

---

## 🔧 Environment Variables Reference

| Variable | Description | Required | Example |
|----------|-------------|----------|---------|
| `ALLOWED_USER_IDS` | Comma-separated Telegram user IDs for whitelist | No (public if empty) | `123456,789012` |
| `ADMIN_API_TOKEN` | Token to protect analytics endpoint | Yes | `secret_token_123` |
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token | Yes | `123456:ABC...` |

---

## 💡 Best Practices

1. **Protect your tokens**: Never commit `ADMIN_API_TOKEN` to git
2. **Monitor analytics regularly**: Check for unusual patterns
3. **Review access logs**: Monitor who's trying to access your bot
4. **Backup analytics data**: Data is stored in `/tmp` (ephemeral on Cloud Run)
5. **Rotate tokens periodically**: Change `ADMIN_API_TOKEN` every few months

---

## 🚨 Troubleshooting

**Problem:** Analytics shows 0 for everything

**Solution:** Make a few requests to the bot first to generate data.

---

**Problem:** "Access denied" when accessing analytics

**Solution:** Check that `ADMIN_API_TOKEN` matches in URL and environment.

---

**Problem:** Bot still public after adding `ALLOWED_USER_IDS`

**Solution:** Verify the environment variable is set correctly:
```bash
gcloud run services describe x-bot-mei \
  --region=europe-west1 \
  --project=xbot-473616 \
  --format="value(spec.template.spec.containers[0].env)"
```

---

**Problem:** Analytics data resets after deploy

**Solution:** Analytics are stored in `/tmp` which is ephemeral. Consider implementing persistent storage (Cloud Storage, Firestore) for long-term analytics.

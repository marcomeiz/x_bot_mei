"""
Analytics tracking system for X Bot Mei.
Tracks usage metrics, errors, and performance.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from collections import defaultdict
from logger_config import logger


class Analytics:
    """In-memory analytics tracker with persistence to disk."""

    def __init__(self, storage_path: str = "/tmp/bot_analytics.json"):
        self.storage_path = storage_path
        self.data = {
            "total_generations": 0,
            "total_comments": 0,
            "total_errors": 0,
            "models_usage": defaultdict(int),
            "commands_usage": defaultdict(int),
            "users_activity": defaultdict(int),
            "daily_stats": defaultdict(lambda: {
                "generations": 0,
                "comments": 0,
                "errors": 0,
                "users": set()
            }),
            "response_times": [],
            "errors_log": [],
            "start_time": datetime.now().isoformat()
        }
        self._load()

    def _load(self):
        """Load analytics from disk if exists."""
        try:
            if os.path.exists(self.storage_path):
                with open(self.storage_path, 'r') as f:
                    stored = json.load(f)
                    # Merge stored data
                    self.data["total_generations"] = stored.get("total_generations", 0)
                    self.data["total_comments"] = stored.get("total_comments", 0)
                    self.data["total_errors"] = stored.get("total_errors", 0)
                    self.data["models_usage"] = defaultdict(int, stored.get("models_usage", {}))
                    self.data["commands_usage"] = defaultdict(int, stored.get("commands_usage", {}))
                    self.data["users_activity"] = defaultdict(int, stored.get("users_activity", {}))
                    self.data["start_time"] = stored.get("start_time", datetime.now().isoformat())
                    logger.info("Analytics loaded from disk")
        except Exception as e:
            logger.error(f"Failed to load analytics: {e}")

    def _save(self):
        """Save analytics to disk."""
        try:
            # Convert to serializable format
            save_data = {
                "total_generations": self.data["total_generations"],
                "total_comments": self.data["total_comments"],
                "total_errors": self.data["total_errors"],
                "models_usage": dict(self.data["models_usage"]),
                "commands_usage": dict(self.data["commands_usage"]),
                "users_activity": dict(self.data["users_activity"]),
                "start_time": self.data["start_time"]
            }
            with open(self.storage_path, 'w') as f:
                json.dump(save_data, f)
        except Exception as e:
            logger.error(f"Failed to save analytics: {e}")

    def track_generation(self, user_id: int, model: str, response_time: float):
        """Track a tweet generation."""
        self.data["total_generations"] += 1
        self.data["models_usage"][model] += 1
        self.data["users_activity"][str(user_id)] += 1
        self.data["commands_usage"]["/g"] += 1
        self.data["response_times"].append(response_time)

        # Track daily
        today = datetime.now().strftime("%Y-%m-%d")
        self.data["daily_stats"][today]["generations"] += 1
        self.data["daily_stats"][today]["users"].add(str(user_id))

        # Keep only last 100 response times
        if len(self.data["response_times"]) > 100:
            self.data["response_times"] = self.data["response_times"][-100:]

        self._save()

    def track_comment(self, user_id: int, response_time: float):
        """Track a comment generation."""
        self.data["total_comments"] += 1
        self.data["users_activity"][str(user_id)] += 1
        self.data["commands_usage"]["/c"] += 1
        self.data["response_times"].append(response_time)

        # Track daily
        today = datetime.now().strftime("%Y-%m-%d")
        self.data["daily_stats"][today]["comments"] += 1
        self.data["daily_stats"][today]["users"].add(str(user_id))

        if len(self.data["response_times"]) > 100:
            self.data["response_times"] = self.data["response_times"][-100:]

        self._save()

    def track_error(self, user_id: int, error_type: str, command: str):
        """Track an error."""
        self.data["total_errors"] += 1

        error_entry = {
            "timestamp": datetime.now().isoformat(),
            "user_id": str(user_id),
            "error_type": error_type,
            "command": command
        }
        self.data["errors_log"].append(error_entry)

        # Track daily
        today = datetime.now().strftime("%Y-%m-%d")
        self.data["daily_stats"][today]["errors"] += 1

        # Keep only last 50 errors
        if len(self.data["errors_log"]) > 50:
            self.data["errors_log"] = self.data["errors_log"][-50:]

        self._save()

    def track_command(self, command: str, user_id: int):
        """Track any command usage."""
        self.data["commands_usage"][command] += 1
        self.data["users_activity"][str(user_id)] += 1
        self._save()

    def get_stats(self) -> Dict:
        """Get all analytics stats."""
        # Calculate uptime
        start = datetime.fromisoformat(self.data["start_time"])
        uptime_seconds = (datetime.now() - start).total_seconds()
        uptime_str = self._format_uptime(uptime_seconds)

        # Calculate averages
        avg_response_time = 0
        if self.data["response_times"]:
            avg_response_time = sum(self.data["response_times"]) / len(self.data["response_times"])

        # Get today's stats
        today = datetime.now().strftime("%Y-%m-%d")
        today_stats = self.data["daily_stats"].get(today, {
            "generations": 0,
            "comments": 0,
            "errors": 0,
            "users": set()
        })

        # Get last 7 days
        last_7_days = self._get_last_n_days_stats(7)

        # Top users
        top_users = sorted(
            self.data["users_activity"].items(),
            key=lambda x: x[1],
            reverse=True
        )[:5]

        return {
            "overview": {
                "uptime": uptime_str,
                "total_generations": self.data["total_generations"],
                "total_comments": self.data["total_comments"],
                "total_errors": self.data["total_errors"],
                "avg_response_time_seconds": round(avg_response_time, 2)
            },
            "today": {
                "generations": today_stats["generations"],
                "comments": today_stats["comments"],
                "errors": today_stats["errors"],
                "unique_users": len(today_stats.get("users", set()))
            },
            "last_7_days": last_7_days,
            "models": dict(self.data["models_usage"]),
            "commands": dict(self.data["commands_usage"]),
            "top_users": [{"user_id": uid, "requests": count} for uid, count in top_users],
            "recent_errors": self.data["errors_log"][-10:]  # Last 10 errors
        }

    def _get_last_n_days_stats(self, n: int) -> Dict:
        """Get stats for last N days."""
        stats = {
            "generations": 0,
            "comments": 0,
            "errors": 0,
            "unique_users": set()
        }

        for i in range(n):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            day_stats = self.data["daily_stats"].get(date, {})
            stats["generations"] += day_stats.get("generations", 0)
            stats["comments"] += day_stats.get("comments", 0)
            stats["errors"] += day_stats.get("errors", 0)
            stats["unique_users"].update(day_stats.get("users", set()))

        stats["unique_users"] = len(stats["unique_users"])
        return stats

    def _format_uptime(self, seconds: float) -> str:
        """Format uptime in human-readable format."""
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        minutes = int((seconds % 3600) // 60)

        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")

        return " ".join(parts) if parts else "< 1m"


# Global analytics instance
analytics = Analytics()

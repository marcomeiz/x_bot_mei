import asyncio
import os
from watcher_v2 import watch_directory

if __name__ == "__main__":
    uploads_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), 'uploads'))
    if not os.path.exists(uploads_dir):
        os.makedirs(uploads_dir)
    
    print("Starting local watcher. Press Ctrl+C to stop.")
    try:
        asyncio.run(watch_directory(uploads_dir))
    except KeyboardInterrupt:
        print("Watcher stopped by user.")

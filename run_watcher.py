import asyncio
import os
from watcher_v2 import watch_directory

if __name__ == "__main__":
    base_dir = os.path.dirname(__file__)
    uploads_dir = os.path.abspath(os.path.join(base_dir, 'uploads'))
    processed_dir = os.path.abspath(os.path.join(base_dir, 'processed_pdfs'))

    os.makedirs(uploads_dir, exist_ok=True)
    os.makedirs(processed_dir, exist_ok=True)
    
    print("Starting local watcher. Press Ctrl+C to stop.")
    try:
        asyncio.run(watch_directory(uploads_dir, processed_dir))
    except KeyboardInterrupt:
        print("Watcher stopped by user.")

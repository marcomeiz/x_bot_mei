import asyncio
import os
import fitz  # PyMuPDF
import json
import urllib.request
from typing import List, Dict, Optional
import sys
import uuid

from dotenv import load_dotenv
from llm_fallback import llm
from embeddings_manager import get_topics_collection

# Load environment variables
load_dotenv()

# Model configuration
TOPIC_EXTRACTION_MODEL = "anthropic/claude-3.5-sonnet"
EMBEDDING_MODEL = "mxbai-embed-large"

def get_embedding_sync(text: str, model: str = EMBEDDING_MODEL):
    try:
        url = "http://localhost:11434/api/embeddings"
        data = json.dumps({"model": model, "prompt": text}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                return json.loads(response.read().decode("utf-8"))["embedding"]
    except Exception as e:
        print(f"Error getting embedding: {e}")
    return None

async def generate_embeddings(topics: List[str]) -> List[List[float]]:
    """Generates embeddings for a list of topics."""
    embeddings = []
    for topic in topics:
        embedding = await asyncio.to_thread(get_embedding_sync, topic)
        if embedding:
            embeddings.append(embedding)
    return embeddings

async def extract_topics(text: str) -> List[str]:
    """Extracts topics from text using the specified model."""
    prompt = f"""
    Extract the key topics from the following text.
    Return the topics as a JSON list of strings.
    For example: ["topic1", "topic2", "topic3"]

    Text:
    {text}
    """
    messages = [{"role": "user", "content": prompt}]
    try:
        topics = await asyncio.to_thread(llm.chat_json, model=TOPIC_EXTRACTION_MODEL, messages=messages)
        if isinstance(topics, list) and all(isinstance(t, str) for t in topics):
            return topics
    except Exception as e:
        print(f"Error extracting topics: {e}")
    return []

async def process_pdf(pdf_path: str) -> Optional[Dict]:
    """
    Processes a single PDF file to extract topics and generate embeddings.
    """
    try:
        print(f"Processing {pdf_path}...")
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()

        if not text.strip():
            print(f"No text found in {pdf_path}")
            return None

        topics = await extract_topics(text)
        if not topics:
            print(f"No topics extracted from {pdf_path}")
            return None

        embeddings = await generate_embeddings(topics)
        if not embeddings:
            print(f"No embeddings generated for {pdf_path}")
            return None

        # Save to ChromaDB
        topics_collection = get_topics_collection()
        ids = [str(uuid.uuid4()) for _ in topics]
        metadatas = [{"source_pdf": os.path.basename(pdf_path)} for _ in topics]
        topics_collection.add(ids=ids, documents=topics, embeddings=embeddings, metadatas=metadatas)

        print(f"Finished processing {pdf_path} and saved to DB.")
        return {"path": pdf_path, "topics": topics, "status": "processed"}
    except Exception as e:
        print(f"Error processing {pdf_path}: {e}")
        return None

async def watch_directory(directory: str) -> None:
    """
    Watches a directory for new PDF files and processes them.
    """
    print(f"Watching directory: {directory}")
    processed_files = set()
    while True:
        for filename in os.listdir(directory):
            if filename.lower().endswith(".pdf") and filename not in processed_files:
                pdf_path = os.path.join(directory, filename)
                result = await process_pdf(pdf_path)
                if result:
                    # TODO: Save the result to the database
                    print(f"Result for {filename}: {result['status']}")
                    processed_files.add(filename)
        await asyncio.sleep(10)

import shutil
import asyncio
import os
import fitz  # PyMuPDF
import json
from typing import List, Dict, Optional
import sys
import uuid

from dotenv import load_dotenv
from llm_fallback import llm
from embeddings_manager import get_topics_collection, get_embedding
from logger_config import logger

# Load environment variables
load_dotenv()

# Model configuration (OpenRouter-only; cheap default)
TOPIC_EXTRACTION_MODEL = os.getenv("TOPIC_EXTRACTION_MODEL", "mistralai/mistral-nemo")

async def generate_embeddings(topics: List[str]) -> List[List[float]]:
    """Generates embeddings for a list of topics."""
    print(f"Generating {len(topics)} embeddings using Google API...")
    embeddings = []
    for topic in topics:
        # Since get_embedding is synchronous, we run it in a thread pool
        embedding = await asyncio.to_thread(get_embedding, topic)
        if embedding:
            embeddings.append(embedding)
    print("Embeddings generated successfully.")
    return embeddings

async def extract_topics(text: str) -> List[str]:
    """Extracts topics from text using the specified model."""
    prompt = f"""
    You are an expert content strategist. Your task is to extract every 'frase de oro' (golden phrase) or 'idea completa' (complete idea) from the provided text.

    These are not single keywords or generic topics. They are insightful, complete sentences or short paragraphs that capture a core, non-obvious idea from the text. Each phrase must be a potential basis for a social media post or a tweet and reflect a strong point of view found in the text.

    Review the entire text and extract every single phrase that meets this criteria. Do not omit any potential candidates.

    Return the results as a JSON list of strings.

    For example:
    ["The biggest barrier to scaling is often the founder's own inability to let go of control.", "You don't need more hours in the day, you need more leverage in your systems."]

    Text:
    {text}
    """
    messages = [{"role": "user", "content": prompt}]
    try:
        response = await asyncio.to_thread(llm.chat_json, model=TOPIC_EXTRACTION_MODEL, messages=messages)
        
        # Handle both direct list and dictionary-wrapped list
        if isinstance(response, list):
            topics = response
        elif isinstance(response, dict) and len(response) == 1:
            key, value = next(iter(response.items()))
            if isinstance(value, list):
                logger.info(f"Extracted topics from dictionary under key '{key}'.")
                topics = value
            else:
                topics = []
        else:
            topics = []

        if all(isinstance(t, str) for t in topics):
            return topics
            
    except Exception as e:
        logger.error(f"Error extracting topics: {e}", exc_info=True)
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

        # Deduplicate topics
        unique_topics = list(set(topics))
        print(f"Found {len(unique_topics)} unique topics out of {len(topics)} extracted.")
        topics = unique_topics

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

async def watch_directory(directory: str, processed_dir: str) -> None:
    """
    Watches a directory for new PDF files, processes them, and moves them.
    """
    print(f"Watching directory: {directory}")
    processed_files = set()
    failed_attempts = {}
    MAX_ATTEMPTS = 3

    while True:
        for filename in os.listdir(directory):
            if filename.lower().endswith(".pdf") and filename not in processed_files:
                pdf_path = os.path.join(directory, filename)
                
                if failed_attempts.get(filename, 0) >= MAX_ATTEMPTS:
                    if filename not in processed_files:
                        print(f"Skipping {filename}, it failed processing {MAX_ATTEMPTS} times.")
                        processed_files.add(filename) # Mark as processed to avoid retrying
                    continue

                result = await process_pdf(pdf_path)
                if result:
                    print(f"Result for {filename}: {result['status']}")
                    # Move the successfully processed file
                    try:
                        destination_path = os.path.join(processed_dir, filename)
                        shutil.move(pdf_path, destination_path)
                        print(f"Moved {filename} to {processed_dir}")
                    except Exception as e:
                        print(f"Error moving file {filename}: {e}")
                    
                    processed_files.add(filename)
                    if filename in failed_attempts:
                        del failed_attempts[filename]
                else:
                    failed_attempts[filename] = failed_attempts.get(filename, 0) + 1
                    print(f"Processing failed for {filename}. Attempt {failed_attempts[filename]}/{MAX_ATTEMPTS}.")
        await asyncio.sleep(10)

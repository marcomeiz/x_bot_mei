#!/usr/bin/env python3
"""
Script de diagnóstico para probar la sincronización de Google Sheets.
Ejecutar manualmente para verificar credenciales y configuración.
"""
import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from logger_config import logger


def test_google_credentials():
    """Verifica que las credenciales de Google estén configuradas."""
    creds_path = os.getenv("GOOGLE_SHEETS_CREDENTIALS_PATH")
    sheet_id = os.getenv("TOPICS_SHEET_ID")

    print("\n" + "="*60)
    print("GOOGLE SHEETS CONFIGURATION TEST")
    print("="*60)

    print(f"\n1. Environment Variables:")
    print(f"   TOPICS_SHEET_ID: {sheet_id or '❌ NOT SET'}")
    print(f"   GOOGLE_SHEETS_CREDENTIALS_PATH: {creds_path or '❌ NOT SET'}")

    if not sheet_id:
        print("\n❌ ERROR: TOPICS_SHEET_ID not set")
        return False

    if not creds_path:
        print("\n❌ ERROR: GOOGLE_SHEETS_CREDENTIALS_PATH not set")
        return False

    if not os.path.exists(creds_path):
        print(f"\n❌ ERROR: Credentials file not found at: {creds_path}")
        return False

    print(f"   ✅ Credentials file exists")

    print(f"\n2. Testing Google Sheets API connection...")
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            creds_path,
            scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
        )
        print("   ✅ Credentials loaded successfully")

        service = build('sheets', 'v4', credentials=creds)
        sheets = service.spreadsheets()

        # Try to read the sheet
        result = sheets.values().get(
            spreadsheetId=sheet_id,
            range='Topics!A1:E1'  # Just read header
        ).execute()

        print(f"   ✅ Successfully connected to Google Sheet")
        print(f"   Header: {result.get('values', [])}")

        # Read all topics
        result = sheets.values().get(
            spreadsheetId=sheet_id,
            range='Topics!A2:E'
        ).execute()

        rows = result.get('values', [])
        print(f"\n3. Topics in Google Sheet:")
        print(f"   Total rows: {len(rows)}")

        valid_topics = 0
        for row in rows:
            if len(row) >= 2 and row[0] and row[1]:
                valid_topics += 1

        print(f"   Valid topics (with ID and Abstract): {valid_topics}")

        if len(rows) > 0:
            print(f"\n   First 3 topics:")
            for i, row in enumerate(rows[:3], 1):
                topic_id = row[0] if len(row) > 0 else "(empty)"
                abstract = row[1][:50] + "..." if len(row) > 1 and len(row[1]) > 50 else row[1] if len(row) > 1 else "(empty)"
                print(f"   {i}. {topic_id}: {abstract}")

        return True

    except ImportError:
        print("\n❌ ERROR: Google API libraries not installed")
        print("   Run: pip install google-auth google-api-python-client")
        return False
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        return False


def test_chromadb_connection():
    """Verifica conexión a ChromaDB."""
    print(f"\n4. Testing ChromaDB connection...")

    chroma_url = os.getenv("CHROMA_DB_URL")
    chroma_path = os.getenv("CHROMA_DB_PATH")

    print(f"   CHROMA_DB_URL: {chroma_url or '(not set)'}")
    print(f"   CHROMA_DB_PATH: {chroma_path or '(not set)'}")

    try:
        from embeddings_manager import get_topics_collection

        topics = get_topics_collection()
        count = topics.count()

        print(f"   ✅ Connected to ChromaDB")
        print(f"   Total topics in ChromaDB: {count}")

        # Get some IDs
        result = topics.get(limit=5, include=['metadatas'])
        print(f"\n   First 5 topic IDs:")
        for i, topic_id in enumerate(result['ids'][:5], 1):
            metadata = result['metadatas'][i-1] if result['metadatas'] else {}
            source = metadata.get('source', 'unknown')
            print(f"   {i}. {topic_id} (source: {source})")

        return True

    except Exception as e:
        print(f"   ❌ ERROR: {e}")
        return False


def main():
    """Run all tests."""
    sheets_ok = test_google_credentials()
    chroma_ok = test_chromadb_connection()

    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"Google Sheets: {'✅ OK' if sheets_ok else '❌ FAILED'}")
    print(f"ChromaDB: {'✅ OK' if chroma_ok else '❌ FAILED'}")
    print("="*60 + "\n")

    if sheets_ok and chroma_ok:
        print("✅ All tests passed! Sync should work.")
        return 0
    else:
        print("❌ Some tests failed. Fix the issues above.")
        return 1


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""
Script para probar la lectura del Google Sheet localmente.
Muestra exactamente qué filas se están leyendo y cuáles se saltan.
"""
import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from logger_config import logger


def test_sheet_reading():
    """Lee el Sheet y muestra estadísticas detalladas."""
    sheet_id = os.getenv('TOPICS_SHEET_ID')
    creds_path = os.getenv('GOOGLE_SHEETS_CREDENTIALS_PATH')

    if not sheet_id:
        print("❌ ERROR: TOPICS_SHEET_ID not set")
        return False

    if not creds_path or not os.path.exists(creds_path):
        print(f"❌ ERROR: GOOGLE_SHEETS_CREDENTIALS_PATH not found at {creds_path}")
        return False

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        print("\n" + "="*70)
        print("GOOGLE SHEET READING TEST")
        print("="*70)

        # Connect to Google Sheets
        creds = service_account.Credentials.from_service_account_file(
            creds_path,
            scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
        )
        service = build('sheets', 'v4', credentials=creds)
        sheets = service.spreadsheets()

        # Read ALL data
        result = sheets.values().get(
            spreadsheetId=sheet_id,
            range='Topics!A2:E'
        ).execute()

        rows = result.get('values', [])
        print(f"\n📊 Total rows in Sheet (excluding header): {len(rows)}")

        # Analyze each row
        valid_topics = []
        skipped_rows = []

        for row_num, row in enumerate(rows, start=2):
            # Check row validity
            if len(row) < 2:
                skipped_rows.append({
                    'row_num': row_num,
                    'reason': 'Less than 2 columns',
                    'data': row
                })
                continue

            topic_id = (row[0] or '').strip()
            abstract = (row[1] or '').strip()

            if not topic_id:
                skipped_rows.append({
                    'row_num': row_num,
                    'reason': 'Empty ID',
                    'data': row
                })
                continue

            if not abstract:
                skipped_rows.append({
                    'row_num': row_num,
                    'reason': 'Empty Abstract',
                    'data': row
                })
                continue

            valid_topics.append({
                'row_num': row_num,
                'id': topic_id,
                'abstract': abstract[:60] + "..." if len(abstract) > 60 else abstract
            })

        # Print summary
        print(f"\n✅ Valid topics: {len(valid_topics)}")
        print(f"❌ Skipped rows: {len(skipped_rows)}")

        # Show first 5 valid topics
        if valid_topics:
            print(f"\n📝 First 5 valid topics:")
            for topic in valid_topics[:5]:
                print(f"  Row {topic['row_num']}: {topic['id']}")
                print(f"    {topic['abstract']}")

        # Show all skipped rows
        if skipped_rows:
            print(f"\n⚠️  Skipped rows (all {len(skipped_rows)}):")
            for skip in skipped_rows:
                print(f"  Row {skip['row_num']}: {skip['reason']}")
                if skip['data']:
                    print(f"    Data: {skip['data']}")
                else:
                    print(f"    Data: (empty)")

        # Show last 5 valid topics
        if len(valid_topics) > 5:
            print(f"\n📝 Last 5 valid topics:")
            for topic in valid_topics[-5:]:
                print(f"  Row {topic['row_num']}: {topic['id']}")
                print(f"    {topic['abstract']}")

        print("\n" + "="*70)
        print("RECOMMENDATIONS:")
        print("="*70)

        if skipped_rows:
            print("\n🔧 To fix skipped rows:")
            print("   1. Open the Google Sheet")
            print("   2. Check the rows listed above")
            print("   3. Fill in missing ID or Abstract fields")
            print("   4. Re-run the sync")
            print("\n   OR if you want auto-generated IDs:")
            print("   - Use /tema command in Telegram")
            print("   - Or we can modify the script to auto-generate IDs")
        else:
            print("\n✅ All rows are valid! No action needed.")

        print("="*70 + "\n")

        return True

    except ImportError:
        print("\n❌ ERROR: Google API libraries not installed")
        print("   Run: pip install google-auth google-api-python-client")
        return False
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    success = test_sheet_reading()
    sys.exit(0 if success else 1)

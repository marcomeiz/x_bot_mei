
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import fitz

from watcher_v2 import process_pdf

@pytest.fixture
def mock_dependencies(mocker):
    mocker.patch('watcher_v2.extract_topics', new_callable=AsyncMock, return_value=["topic1", "topic2"])
    mocker.patch('watcher_v2.generate_embeddings', new_callable=AsyncMock, return_value=[[0.1]*1024, [0.2]*1024])
    mocker.patch('watcher_v2.get_topics_collection', return_value=MagicMock())

@pytest.mark.asyncio
async def test_process_pdf_success(mock_dependencies, tmp_path):
    """Test successful processing of a PDF file."""
    # Create a dummy PDF in a temporary directory
    pdf_path = tmp_path / "dummy.pdf"
    doc = fitz.open() 
    page = doc.new_page()
    page.insert_text((50, 72), "This is a test PDF.")
    doc.save(str(pdf_path))
    doc.close()

    result = await process_pdf(str(pdf_path))

    assert result is not None
    assert result['status'] == 'processed'
    assert result['topics'] == ["topic1", "topic2"]

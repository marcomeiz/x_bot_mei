#!/usr/bin/env python3
"""
Audit Topics for ICP Fit

Scans all topics in ChromaDB to find those that assume infrastructure
the ICP (solopreneur Day 1-Year 1) doesn't have.

Detects topics about:
- Escalation paths (assumes team hierarchy)
- Ticket systems (assumes ticketing infrastructure)
- Team rituals (assumes staff/employees)
- Delegation frameworks (assumes team to delegate to)
- On-call rotations (assumes on-call team)
+ 11 more red flags for solo operators

Output:
- ❌ FAIL: Topics that speak to wrong audience (need DELETE or REWRITE)
- ✅ PASS: Topics appropriate for solo operators
- Summary report with recommendations
"""
import os
import sys
import json
from pathlib import Path
from typing import List, Dict, Tuple
from dataclasses import dataclass
import chromadb
from chromadb.config import Settings as ChromaSettings
from dotenv import load_dotenv

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load environment before importing local modules
load_dotenv()

from logger_config import logger
from rules import validate_icp_fit
from prompt_context import build_prompt_context


def get_topics_collection():
    """
    Get topics collection from ChromaDB.
    Simplified version without embeddings_manager dependency.
    """
    chroma_url = os.getenv("CHROMA_DB_URL", "").strip()

    if chroma_url:
        # Remote ChromaDB
        from urllib.parse import urlparse
        parsed = urlparse(chroma_url)
        host = parsed.hostname or chroma_url
        port = parsed.port or (443 if (parsed.scheme or "http").lower() == "https" else 80)
        ssl = (parsed.scheme or "http").lower() == "https"
        logger.info(f"Connecting to ChromaDB HTTP (host={host}, port={port}, ssl={ssl})")
        client = chromadb.HttpClient(
            host=host,
            port=port,
            ssl=ssl,
            settings=ChromaSettings(anonymized_telemetry=False)
        )
    else:
        # Local ChromaDB
        persist_dir = "db/"
        logger.info(f"Connecting to local ChromaDB (persist_directory={persist_dir})")
        client = chromadb.PersistentClient(
            path=persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False)
        )

    collection_name = os.getenv("TOPICS_COLLECTION", "topics_collection")
    logger.info(f"Using topics collection: '{collection_name}'")
    return client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"}
    )


@dataclass
class TopicAuditResult:
    """Result of auditing a single topic."""
    topic_id: str
    abstract: str
    passed: bool
    failure_reason: str
    source_pdf: str
    recommendation: str  # DELETE, REWRITE, or OK


def audit_all_topics() -> Tuple[List[TopicAuditResult], Dict[str, int]]:
    """
    Audit all topics in ChromaDB for ICP fit.

    Returns:
        Tuple of (audit_results, summary_stats)
    """
    logger.info("Starting topic ICP-fit audit...")

    # Load ICP from context
    context = build_prompt_context()
    icp_text = context.icp
    logger.info(f"ICP loaded: {icp_text[:100]}...")

    # Get all topics from ChromaDB
    topics_collection = get_topics_collection()
    count = topics_collection.count()
    logger.info(f"Found {count} topics in ChromaDB")

    result = topics_collection.get(
        limit=max(count, 1000),  # Safety margin
        include=['documents', 'metadatas']
    )

    # Audit each topic
    audit_results = []
    stats = {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "red_flags": {}  # Count by red flag type
    }

    for i in range(len(result['ids'])):
        topic_id = result['ids'][i]
        abstract = result['documents'][i]
        metadata = result.get('metadatas', [{}])[i] or {}
        source_pdf = metadata.get('source_pdf', 'unknown')

        stats["total"] += 1

        # Validate ICP fit
        passed, failure_reason = validate_icp_fit(abstract, icp_text)

        if passed:
            stats["passed"] += 1
            audit_results.append(TopicAuditResult(
                topic_id=topic_id,
                abstract=abstract,
                passed=True,
                failure_reason="",
                source_pdf=source_pdf,
                recommendation="OK"
            ))
        else:
            stats["failed"] += 1

            # Extract red flag type from failure reason
            # Format: "Assumes infrastructure solo operator doesn't have: 'escalation path' (assumes team hierarchy)"
            if "'" in failure_reason:
                red_flag = failure_reason.split("'")[1]
                stats["red_flags"][red_flag] = stats["red_flags"].get(red_flag, 0) + 1

            # Determine recommendation
            recommendation = _get_recommendation(abstract, failure_reason)

            audit_results.append(TopicAuditResult(
                topic_id=topic_id,
                abstract=abstract,
                passed=False,
                failure_reason=failure_reason,
                source_pdf=source_pdf,
                recommendation=recommendation
            ))

            logger.warning(f"❌ FAIL: {topic_id[:30]}... | {failure_reason}")

    logger.info(f"Audit complete: {stats['passed']}/{stats['total']} passed, {stats['failed']} failed")
    return audit_results, stats


def _get_recommendation(abstract: str, failure_reason: str) -> str:
    """
    Determine if topic should be DELETED or REWRITTEN.

    DELETE: Topic fundamentally about team/enterprise infrastructure
    REWRITE: Topic could be adapted for solo operators
    """
    abstract_lower = abstract.lower()

    # Topics fundamentally about teams/enterprise (DELETE)
    delete_markers = [
        "escalation", "org chart", "delegation framework",
        "team ritual", "stand-up", "sprint planning",
        "sla", "on-call rotation", "incident response"
    ]

    if any(marker in abstract_lower for marker in delete_markers):
        return "DELETE"

    # Topics that could be reframed for solo operators (REWRITE)
    rewrite_markers = [
        "ticket system", "support", "customer",
        "process", "workflow", "system"
    ]

    if any(marker in abstract_lower for marker in rewrite_markers):
        return "REWRITE"

    # Default: needs human review
    return "REWRITE"


def generate_report(audit_results: List[TopicAuditResult], stats: Dict[str, int]):
    """
    Generate human-readable audit report.
    """
    print("\n" + "="*80)
    print("🔍 TOPIC ICP-FIT AUDIT REPORT")
    print("="*80)

    print(f"\n📊 Summary:")
    print(f"  Total topics:    {stats['total']}")
    print(f"  ✅ Passed:        {stats['passed']} ({stats['passed']/stats['total']*100:.1f}%)")
    print(f"  ❌ Failed:        {stats['failed']} ({stats['failed']/stats['total']*100:.1f}%)")

    if stats['red_flags']:
        print(f"\n🚩 Red Flags Detected:")
        for red_flag, count in sorted(stats['red_flags'].items(), key=lambda x: x[1], reverse=True):
            print(f"  - '{red_flag}': {count} topics")

    # Group failures by recommendation
    deletes = [r for r in audit_results if r.recommendation == "DELETE"]
    rewrites = [r for r in audit_results if r.recommendation == "REWRITE"]

    print(f"\n🗑️  DELETE Recommendations: {len(deletes)}")
    if deletes:
        print("\n  Topics fundamentally about team/enterprise infrastructure:")
        for result in deletes[:10]:  # Show first 10
            print(f"\n  ID: {result.topic_id}")
            print(f"  Abstract: {result.abstract[:100]}...")
            print(f"  Reason: {result.failure_reason}")
            print(f"  Source: {result.source_pdf}")

    print(f"\n✏️  REWRITE Recommendations: {len(rewrites)}")
    if rewrites:
        print("\n  Topics that could be adapted for solo operators:")
        for result in rewrites[:10]:  # Show first 10
            print(f"\n  ID: {result.topic_id}")
            print(f"  Abstract: {result.abstract[:100]}...")
            print(f"  Reason: {result.failure_reason}")
            print(f"  Source: {result.source_pdf}")

    # Save full report to JSON
    output_file = 'data/topics_icp_audit.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            "stats": stats,
            "deletes": [
                {
                    "id": r.topic_id,
                    "abstract": r.abstract,
                    "reason": r.failure_reason,
                    "source": r.source_pdf
                } for r in deletes
            ],
            "rewrites": [
                {
                    "id": r.topic_id,
                    "abstract": r.abstract,
                    "reason": r.failure_reason,
                    "source": r.source_pdf
                } for r in rewrites
            ],
            "passed": [
                {
                    "id": r.topic_id,
                    "abstract": r.abstract,
                    "source": r.source_pdf
                } for r in audit_results if r.passed
            ]
        }, f, indent=2, ensure_ascii=False)

    print(f"\n💾 Full report saved to: {output_file}")
    print("\n" + "="*80)


def main():
    """Run topic ICP-fit audit."""
    try:
        audit_results, stats = audit_all_topics()
        generate_report(audit_results, stats)

        # Exit with error code if failures found (for CI/CD)
        if stats['failed'] > 0:
            sys.exit(1)

    except Exception as e:
        logger.error(f"Audit failed: {e}", exc_info=True)
        print(f"\n❌ Audit failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()

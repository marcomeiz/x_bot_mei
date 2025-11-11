#!/usr/bin/env python3
"""
Test ICP-fit validation on sample topics.

Demonstrates how validate_icp_fit() catches topics inappropriate for solopreneurs.
"""
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from rules import validate_icp_fit


# ICP definition (from config/icp.md)
ICP = """TARGET AUDIENCE:
Solopreneurs (Day 1 - Year 1). Solo operator drowning in chaos—no systems, everything in their head. Craft-lover who fears ops. Needs step-zero tactical help, not theory.

PAIN: They're alone in the fucking world. They ARE every function—sales, delivery, support, ops. It's challenging but also brutal. No one to share the load.

VOICE THEY RESPOND TO: Trench veteran—tactical, no-bullshit, practical. NOT corporate consultant or general on a hill."""


# Test topics (mix of good and bad for solopreneurs)
TEST_TOPICS = [
    # ❌ BAD: Assumes team/infrastructure
    {
        "id": "bad-1",
        "abstract": "Escalation path: who, when, how; practice before chaos",
        "expected": False,
        "reason": "Assumes escalation path (team hierarchy)"
    },
    {
        "id": "bad-2",
        "abstract": "Run your ticket system when nothing's on fire. File a dummy ticket to test the chain.",
        "expected": False,
        "reason": "Assumes ticket system (ticketing infrastructure)"
    },
    {
        "id": "bad-3",
        "abstract": "Team rituals that surface blockers before they become fires",
        "expected": False,
        "reason": "Assumes team (solo has no team)"
    },
    {
        "id": "bad-4",
        "abstract": "Delegation frameworks: what to delegate, what to keep, how to hand off cleanly",
        "expected": False,
        "reason": "Assumes team to delegate to"
    },
    {
        "id": "bad-5",
        "abstract": "On-call rotation best practices: balancing coverage with burnout prevention",
        "expected": False,
        "reason": "Assumes on-call team (solo has no rotation)"
    },

    # ✅ GOOD: Appropriate for solopreneurs
    {
        "id": "good-1",
        "abstract": "When you're the only one: who to call when shit breaks at 3am and you're stuck",
        "expected": True,
        "reason": "Speaks to solo operator reality"
    },
    {
        "id": "good-2",
        "abstract": "Three Google Sheets formulas that replace hiring a data person (Day 1 tactic)",
        "expected": True,
        "reason": "Solo workaround, no team assumed"
    },
    {
        "id": "good-3",
        "abstract": "Your first paying customer just broke production. What to do in the next 10 minutes.",
        "expected": True,
        "reason": "Solo operator crisis, no infrastructure assumed"
    },
    {
        "id": "good-4",
        "abstract": "Stop building features. Your first system should be: how to not lose money while you sleep.",
        "expected": True,
        "reason": "Solo operator priorities, no team/systems"
    },
    {
        "id": "good-5",
        "abstract": "Manual workarounds that scale to $10K MRR before you need 'proper' systems",
        "expected": True,
        "reason": "Solo operator scrappiness"
    },
]


def test_icp_validation():
    """Test ICP-fit validation on sample topics."""
    print("\n" + "="*80)
    print("🔍 ICP-FIT VALIDATION TEST")
    print("="*80)

    passed = 0
    failed = 0
    false_positives = 0
    false_negatives = 0

    for topic in TEST_TOPICS:
        topic_id = topic["id"]
        abstract = topic["abstract"]
        expected = topic["expected"]
        reason = topic["reason"]

        # Validate
        result, validation_reason = validate_icp_fit(abstract, ICP)

        # Check if result matches expectation
        if result == expected:
            passed += 1
            status = "✅ CORRECT"
        else:
            failed += 1
            if result and not expected:
                false_positives += 1
                status = "❌ FALSE POSITIVE"
            else:
                false_negatives += 1
                status = "❌ FALSE NEGATIVE"

        print(f"\n{status} | {topic_id}")
        print(f"  Abstract: {abstract}")
        print(f"  Expected: {'PASS' if expected else 'FAIL'} ({reason})")
        print(f"  Got: {'PASS' if result else 'FAIL'}")
        if not result:
            print(f"  Validation: {validation_reason}")

    print("\n" + "="*80)
    print(f"📊 RESULTS:")
    print(f"  Total: {len(TEST_TOPICS)}")
    print(f"  ✅ Correct: {passed} ({passed/len(TEST_TOPICS)*100:.1f}%)")
    print(f"  ❌ Failed: {failed}")
    if false_positives:
        print(f"  ⚠️  False Positives: {false_positives} (passed when should fail)")
    if false_negatives:
        print(f"  ⚠️  False Negatives: {false_negatives} (failed when should pass)")
    print("="*80 + "\n")

    if failed > 0:
        sys.exit(1)


if __name__ == '__main__':
    test_icp_validation()

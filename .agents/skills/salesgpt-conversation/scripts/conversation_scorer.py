#!/usr/bin/env python3
"""
Sales Conversation Scorer

Evaluates the quality of a sales conversation across multiple dimensions.
Adapted from SalesGPT conversation patterns.

Usage:
  python conversation_scorer.py --history-file conversation.txt
  python conversation_scorer.py --history "Agent: Hi!|User: Hello" --agent-name "Tyler"
"""

import argparse
import json
import re
import sys


def score_conversation(history: str, agent_name: str = "Agent") -> dict:
    """Score a sales conversation across key dimensions."""
    lines = [l.strip() for l in history.strip().split("\n") if l.strip()]

    agent_lines = [l for l in lines if l.startswith(f"{agent_name}:")]
    user_lines = [l for l in lines if l.startswith("User:")]

    total_turns = len(lines)
    agent_turns = len(agent_lines)
    user_turns = len(user_lines)

    scores = {}

    # 1. Response Length Score (shorter is better in sales)
    if agent_lines:
        avg_agent_words = sum(len(l.split()) for l in agent_lines) / len(agent_lines)
        if avg_agent_words <= 30:
            scores["brevity"] = {"score": 10, "detail": f"Excellent — avg {avg_agent_words:.0f} words/turn"}
        elif avg_agent_words <= 50:
            scores["brevity"] = {"score": 7, "detail": f"Good — avg {avg_agent_words:.0f} words/turn"}
        elif avg_agent_words <= 80:
            scores["brevity"] = {"score": 4, "detail": f"Too verbose — avg {avg_agent_words:.0f} words/turn"}
        else:
            scores["brevity"] = {"score": 2, "detail": f"Way too long — avg {avg_agent_words:.0f} words/turn"}
    else:
        scores["brevity"] = {"score": 0, "detail": "No agent messages found"}

    # 2. Question Asking Score (needs analysis quality)
    questions_asked = sum(1 for l in agent_lines if "?" in l)
    open_ended = sum(1 for l in agent_lines if any(w in l.lower() for w in
        ["what", "how", "tell me", "describe", "explain", "walk me through"]))

    if questions_asked >= 3 and open_ended >= 2:
        scores["discovery"] = {"score": 10, "detail": f"{questions_asked} questions, {open_ended} open-ended"}
    elif questions_asked >= 2:
        scores["discovery"] = {"score": 7, "detail": f"{questions_asked} questions, {open_ended} open-ended"}
    elif questions_asked >= 1:
        scores["discovery"] = {"score": 4, "detail": f"Only {questions_asked} question(s) — need more discovery"}
    else:
        scores["discovery"] = {"score": 1, "detail": "No questions asked — missing needs analysis"}

    # 3. Listen-to-Talk Ratio
    if agent_turns > 0 and user_turns > 0:
        agent_words = sum(len(l.split()) for l in agent_lines)
        user_words = sum(len(l.split()) for l in user_lines)
        ratio = user_words / agent_words if agent_words > 0 else 0

        if ratio >= 1.5:
            scores["listening"] = {"score": 10, "detail": f"Excellent — prospect talks {ratio:.1f}x more"}
        elif ratio >= 1.0:
            scores["listening"] = {"score": 8, "detail": f"Good — prospect talks {ratio:.1f}x more"}
        elif ratio >= 0.5:
            scores["listening"] = {"score": 5, "detail": f"Talking too much — ratio {ratio:.1f}x"}
        else:
            scores["listening"] = {"score": 2, "detail": f"Dominating conversation — ratio {ratio:.1f}x"}
    else:
        scores["listening"] = {"score": 5, "detail": "Insufficient data"}

    # 4. Personalization Score
    personalization_signals = 0
    for line in agent_lines:
        lower = line.lower()
        if any(phrase in lower for phrase in [
            "you mentioned", "as you said", "based on what you",
            "your team", "your company", "your situation",
            "sounds like you", "i hear you"
        ]):
            personalization_signals += 1

    if personalization_signals >= 3:
        scores["personalization"] = {"score": 10, "detail": f"{personalization_signals} personalized references"}
    elif personalization_signals >= 2:
        scores["personalization"] = {"score": 7, "detail": f"{personalization_signals} personalized references"}
    elif personalization_signals >= 1:
        scores["personalization"] = {"score": 5, "detail": "Minimal personalization"}
    else:
        scores["personalization"] = {"score": 2, "detail": "No personalization detected — generic pitch"}

    # 5. Call-to-Action Score
    cta_signals = sum(1 for l in agent_lines if any(w in l.lower() for w in
        ["schedule", "demo", "trial", "meeting", "next step", "follow up",
         "send you", "calendar", "book", "sign up", "let's set up"]))

    if cta_signals >= 2:
        scores["call_to_action"] = {"score": 10, "detail": f"{cta_signals} CTAs — good progression"}
    elif cta_signals >= 1:
        scores["call_to_action"] = {"score": 7, "detail": f"{cta_signals} CTA — could push more"}
    else:
        scores["call_to_action"] = {"score": 3, "detail": "No clear CTA — conversation may stall"}

    # 6. Objection Handling Score
    objection_indicators = sum(1 for l in user_lines if any(w in l.lower() for w in
        ["but", "concerned", "expensive", "not sure", "competitor", "already have",
         "don't need", "too much", "risky"]))
    handled = sum(1 for l in agent_lines if any(w in l.lower() for w in
        ["understand", "fair point", "great question", "let me address",
         "many customers feel", "that's common"]))

    if objection_indicators == 0:
        scores["objection_handling"] = {"score": 7, "detail": "No objections raised (may not have reached that stage)"}
    elif handled >= objection_indicators:
        scores["objection_handling"] = {"score": 10, "detail": f"All {objection_indicators} objection(s) addressed"}
    elif handled > 0:
        scores["objection_handling"] = {"score": 5, "detail": f"Addressed {handled}/{objection_indicators} objections"}
    else:
        scores["objection_handling"] = {"score": 2, "detail": f"{objection_indicators} objection(s) not addressed"}

    # Overall score
    total = sum(s["score"] for s in scores.values())
    max_total = len(scores) * 10
    overall = round((total / max_total) * 100) if max_total > 0 else 0

    # Grade
    if overall >= 85:
        grade = "A"
    elif overall >= 70:
        grade = "B"
    elif overall >= 55:
        grade = "C"
    elif overall >= 40:
        grade = "D"
    else:
        grade = "F"

    return {
        "overall_score": overall,
        "grade": grade,
        "total_turns": total_turns,
        "agent_turns": agent_turns,
        "user_turns": user_turns,
        "dimensions": scores,
        "recommendations": generate_recommendations(scores),
    }


def generate_recommendations(scores: dict) -> list:
    """Generate actionable recommendations based on scores."""
    recs = []
    for dimension, data in scores.items():
        if data["score"] <= 4:
            if dimension == "brevity":
                recs.append("Shorten responses — aim for 20-30 words per turn")
            elif dimension == "discovery":
                recs.append("Ask more open-ended questions before presenting solutions")
            elif dimension == "listening":
                recs.append("Let the prospect talk more — aim for 60/40 prospect-to-agent ratio")
            elif dimension == "personalization":
                recs.append("Reference prospect's specific situation and language in responses")
            elif dimension == "call_to_action":
                recs.append("Include a clear next-step ask (demo, trial, meeting)")
            elif dimension == "objection_handling":
                recs.append("Acknowledge objections before addressing them (LAER framework)")
    return recs


def main():
    parser = argparse.ArgumentParser(description="Sales Conversation Scorer")
    parser.add_argument("--history", type=str, help="Conversation history (pipe-separated)")
    parser.add_argument("--history-file", type=str, help="Path to conversation history file")
    parser.add_argument("--agent-name", type=str, default="Agent", help="Agent name prefix")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    if args.history:
        history = args.history.replace("|", "\n")
    elif args.history_file:
        with open(args.history_file, "r") as f:
            history = f.read()
    else:
        print("Error: Provide --history or --history-file")
        sys.exit(1)

    result = score_conversation(history, args.agent_name)

    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"\n{'='*50}")
        print(f"  SALES CONVERSATION SCORECARD")
        print(f"{'='*50}")
        print(f"  Overall: {result['overall_score']}% (Grade: {result['grade']})")
        print(f"  Turns: {result['total_turns']} ({result['agent_turns']} agent, {result['user_turns']} user)")
        print(f"{'='*50}")
        print()

        for dim, data in result["dimensions"].items():
            bar = "█" * data["score"] + "░" * (10 - data["score"])
            print(f"  {dim:<20} {bar} {data['score']}/10")
            print(f"  {'':20} {data['detail']}")
            print()

        if result["recommendations"]:
            print(f"{'='*50}")
            print("  RECOMMENDATIONS")
            print(f"{'='*50}")
            for i, rec in enumerate(result["recommendations"], 1):
                print(f"  {i}. {rec}")
            print()


if __name__ == "__main__":
    main()

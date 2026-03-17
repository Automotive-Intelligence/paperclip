#!/usr/bin/env python3
"""
Sales Conversation Stage Analyzer

Adapted from SalesGPT (https://github.com/filip-michalsky/SalesGPT)
Analyzes conversation history and determines optimal conversation stage.

Usage:
  python stage_analyzer.py --history "Agent: Hi there!|User: Hello, who is this?" --current-stage 1
  python stage_analyzer.py --history-file conversation.txt --current-stage 3
"""

import argparse
import json
import sys

CONVERSATION_STAGES = {
    "1": "Introduction: Start the conversation by introducing yourself and your company. Be polite and respectful while keeping the tone of the conversation professional.",
    "2": "Qualification: Qualify the prospect by confirming if they are the right person to talk to regarding your product/service.",
    "3": "Value proposition: Briefly explain how your product/service can benefit the prospect. Focus on unique selling points.",
    "4": "Needs analysis: Ask open-ended questions to uncover the prospect's needs and pain points.",
    "5": "Solution presentation: Based on the prospect's needs, present your product/service as the solution.",
    "6": "Objection handling: Address any objections that the prospect may have. Provide evidence or testimonials.",
    "7": "Close: Ask for the sale by proposing a next step. Summarize and reiterate benefits.",
    "8": "End conversation: The prospect has to leave, is not interested, or next steps were determined.",
}

# Signal keywords that suggest stage transitions
STAGE_SIGNALS = {
    "advance": {
        "1->2": ["who is this", "what do you do", "tell me more", "go ahead"],
        "2->3": ["yes i'm the", "i handle", "i'm responsible", "that's me", "i make those decisions"],
        "3->4": ["interesting", "tell me more", "how does that work", "sounds good"],
        "4->5": ["our biggest challenge", "we struggle with", "pain point", "problem is"],
        "5->6": ["but what about", "i'm concerned", "how much", "what if", "competitors"],
        "6->7": ["that makes sense", "i'm convinced", "sounds good", "let's do it", "what's next"],
        "7->8": ["sounds great", "let's schedule", "send me the details", "i'll sign up"],
    },
    "retreat": {
        "->8": ["not interested", "no thanks", "don't call", "remove me", "goodbye", "have to go"],
        "->4": ["actually i have another question", "what about", "can you also"],
        "->6": ["wait but", "i'm not sure about", "my concern is"],
    },
}


def analyze_stage(conversation_history: str, current_stage: int) -> dict:
    """Analyze conversation history and recommend next stage."""
    history_lower = conversation_history.lower()
    lines = conversation_history.strip().split("\n")

    # Get last user message
    last_user_msg = ""
    for line in reversed(lines):
        if line.strip().startswith("User:"):
            last_user_msg = line.replace("User:", "").strip().lower()
            break

    recommended_stage = current_stage
    reason = "Staying in current stage — awaiting more prospect input"

    # Check for retreat signals first (higher priority)
    for target, signals in STAGE_SIGNALS["retreat"].items():
        for signal in signals:
            if signal in last_user_msg:
                stage_num = int(target.replace("->", ""))
                recommended_stage = stage_num
                reason = f"Prospect signal detected: '{signal}' → moving to Stage {stage_num}"
                break

    # Check for advance signals
    if recommended_stage == current_stage:
        stage_key = f"{current_stage}->{current_stage + 1}"
        if stage_key in STAGE_SIGNALS["advance"]:
            for signal in STAGE_SIGNALS["advance"][stage_key]:
                if signal in last_user_msg:
                    recommended_stage = current_stage + 1
                    reason = f"Prospect signal detected: '{signal}' → advancing to Stage {recommended_stage}"
                    break

    # Empty history always starts at 1
    if not conversation_history.strip():
        recommended_stage = 1
        reason = "Empty conversation history — starting with Introduction"

    return {
        "current_stage": current_stage,
        "current_stage_name": CONVERSATION_STAGES.get(str(current_stage), "Unknown"),
        "recommended_stage": recommended_stage,
        "recommended_stage_name": CONVERSATION_STAGES.get(str(recommended_stage), "Unknown"),
        "stage_changed": recommended_stage != current_stage,
        "reason": reason,
        "conversation_turns": len([l for l in lines if l.strip()]),
        "last_user_message": last_user_msg,
    }


def main():
    parser = argparse.ArgumentParser(description="Sales Conversation Stage Analyzer")
    parser.add_argument("--history", type=str, help="Conversation history (pipe-separated turns)")
    parser.add_argument("--history-file", type=str, help="Path to conversation history file")
    parser.add_argument("--current-stage", type=int, default=1, help="Current conversation stage (1-8)")
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format")
    args = parser.parse_args()

    if args.history:
        history = args.history.replace("|", "\n")
    elif args.history_file:
        with open(args.history_file, "r") as f:
            history = f.read()
    else:
        history = ""

    result = analyze_stage(history, args.current_stage)

    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"Current Stage:     {result['current_stage']} - {result['current_stage_name'][:60]}")
        print(f"Recommended Stage: {result['recommended_stage']} - {result['recommended_stage_name'][:60]}")
        print(f"Stage Changed:     {'Yes' if result['stage_changed'] else 'No'}")
        print(f"Reason:            {result['reason']}")
        print(f"Conversation Turns: {result['conversation_turns']}")
        if result['last_user_message']:
            print(f"Last User Message: {result['last_user_message'][:80]}")


if __name__ == "__main__":
    main()

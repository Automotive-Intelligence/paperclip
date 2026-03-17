#!/usr/bin/env python3
"""
SalesGPT Interactive Conversation Runner

Runs a live sales conversation using the SalesGPT framework with stage-based
progression, leveraging the existing agent configs and LLM setup.

Usage:
  python run_salesgpt.py                          # Default: Tyler Brooks (Automotive Intelligence)
  python run_salesgpt.py --agent tyler             # Tyler Brooks
  python run_salesgpt.py --agent marcus            # Marcus Chen
  python run_salesgpt.py --agent ryan              # Ryan Data
  python run_salesgpt.py --agent tyler --score     # Score the conversation at the end
  python run_salesgpt.py --demo                    # Run a demo conversation (no API key needed)

Environment:
  OPENAI_API_KEY    - Required for live mode (OpenRouter or OpenAI)
  OPENAI_BASE_URL   - LLM endpoint (default: https://openrouter.ai/api/v1)
  MODEL_ID          - Model to use (default: gpt-4o-mini)
  ANTHROPIC_API_KEY  - Alternative: uses Claude via Anthropic API
"""

import argparse
import json
import os
import sys

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

# Import stage analyzer and scorer
sys.path.insert(0, os.path.join(PROJECT_ROOT, ".agents", "skills", "salesgpt-conversation", "scripts"))
from stage_analyzer import analyze_stage, CONVERSATION_STAGES
from conversation_scorer import score_conversation

# ── Agent Configs ────────────────────────────────────────────────────────────

EXAMPLES_DIR = os.path.join(
    PROJECT_ROOT, ".agents", "skills", "salesgpt-conversation", "examples"
)

AGENT_CONFIGS = {
    "tyler": os.path.join(EXAMPLES_DIR, "automotive-intelligence-agent.json"),
    "marcus": os.path.join(EXAMPLES_DIR, "meridian-grove-agent.json"),
    "ryan": os.path.join(EXAMPLES_DIR, "ryan-data-agent.json"),
}


def load_agent_config(agent_key: str) -> dict:
    path = AGENT_CONFIGS.get(agent_key)
    if not path or not os.path.exists(path):
        print(f"Error: Agent config not found for '{agent_key}'")
        print(f"Available agents: {', '.join(AGENT_CONFIGS.keys())}")
        sys.exit(1)
    with open(path, "r") as f:
        return json.load(f)


def build_system_prompt(config: dict) -> str:
    """Build the SalesGPT system prompt from agent config."""
    if config.get("use_custom_prompt") == "True" and config.get("custom_prompt"):
        template = config["custom_prompt"]
        prompt = template.replace("{salesperson_name}", config["salesperson_name"])
        prompt = prompt.replace("{salesperson_role}", config["salesperson_role"])
        prompt = prompt.replace("{company_name}", config["company_name"])
        prompt = prompt.replace("{company_business}", config["company_business"])
        prompt = prompt.replace("{company_values}", config["company_values"])
        prompt = prompt.replace("{conversation_purpose}", config["conversation_purpose"])
        prompt = prompt.replace("{conversation_type}", config["conversation_type"])
        prompt = prompt.replace("\nConversation history:\n{conversation_history}", "")
        prompt = prompt.replace("{conversation_history}", "")
        return prompt

    return (
        f"Never forget your name is {config['salesperson_name']}. "
        f"You work as a {config['salesperson_role']}.\n"
        f"You work at company named {config['company_name']}. "
        f"{config['company_name']}'s business is the following: {config['company_business']}.\n"
        f"Company values are the following. {config['company_values']}\n"
        f"You are contacting a potential prospect in order to {config['conversation_purpose']}\n"
        f"Your means of contacting the prospect is {config['conversation_type']}\n\n"
        "If you're asked about where you got the user's contact information, say that you got it from public records.\n"
        "Keep your responses in short length to retain the user's attention. Never produce lists, just answers.\n"
        "Start the conversation by just a greeting and how is the prospect doing without pitching in your first turn.\n"
        "When the conversation is over, output <END_OF_CALL>\n"
        "Always think about at which conversation stage you are at before answering.\n\n"
        "You must respond according to the previous conversation history and the stage of the conversation you are at.\n"
        "Only generate one response at a time. When you are done generating, end with '<END_OF_TURN>'."
    )


# ── LLM Backend ─────────────────────────────────────────────────────────────

def get_llm_client():
    """Initialize litellm with the best available API key."""
    import litellm

    # Check for Anthropic key first
    if os.getenv("ANTHROPIC_API_KEY"):
        return {
            "model": os.getenv("MODEL_ID", "anthropic/claude-haiku-4-5-20251001"),
            "api_key": os.getenv("ANTHROPIC_API_KEY"),
        }

    # OpenAI / OpenRouter
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    base_url = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    model = os.getenv("MODEL_ID", "gpt-4o-mini")

    # For OpenRouter, use openai/ prefix with litellm
    if "openrouter" in base_url:
        return {"model": f"openrouter/{model}", "api_key": api_key}

    return {"model": model, "api_key": api_key, "base_url": base_url}


def generate_response(llm_config: dict, system_prompt: str, conversation_history: str,
                       stage_info: dict, agent_name: str) -> str:
    """Generate a sales agent response using litellm."""
    import litellm

    stage_instruction = (
        f"\n\nCurrent conversation stage: {stage_info['recommended_stage']} - "
        f"{stage_info['recommended_stage_name']}\n"
    )

    messages = [
        {"role": "system", "content": system_prompt + stage_instruction},
    ]

    if conversation_history.strip():
        messages.append({
            "role": "user",
            "content": f"Conversation so far:\n{conversation_history}\n\n{agent_name}:"
        })
    else:
        messages.append({
            "role": "user",
            "content": f"Start the conversation. Respond as {agent_name}:"
        })

    try:
        response = litellm.completion(
            messages=messages,
            max_tokens=200,
            temperature=0.7,
            **llm_config,
        )
        text = response.choices[0].message.content.strip()
        text = text.replace("<END_OF_TURN>", "").strip()
        return text
    except Exception as e:
        return f"[LLM Error: {type(e).__name__}: {e}]"


# ── Demo Mode ────────────────────────────────────────────────────────────────

DEMO_CONVERSATION = [
    ("Tyler", "Hey, good morning! How are you doing today?"),
    ("User", "Hi, I'm doing well. Who is this?"),
    ("Tyler", "This is Tyler Brooks from Automotive Intelligence. I'm reaching out because I noticed your dealership recently expanded its service department. How's that going?"),
    ("User", "It's been challenging, actually. We're struggling with diagnostic turnaround times."),
    ("Tyler", "I hear that a lot. What's your average diagnostic time right now?"),
    ("User", "About 2 hours per vehicle, sometimes more for complex issues."),
    ("Tyler", "That's pretty common. Our AI diagnostic platform has helped dealerships like yours cut that down to about 45 minutes on average. Would it be helpful if I showed you how it works with a quick 15-minute demo?"),
    ("User", "That sounds interesting. When are you available?"),
    ("Tyler", "I can do Thursday at 2pm or Friday at 10am — which works better for you?"),
    ("User", "Thursday at 2pm works."),
    ("Tyler", "Perfect, I'll send over a calendar invite. Looking forward to showing you what we can do. Have a great rest of your day!"),
]


def run_demo(config: dict):
    """Run the demo conversation showing stage progression and scoring."""
    agent_name = config["salesperson_name"].split()[0]
    print()
    print("  Running demo conversation...\n")

    conversation_history = ""
    current_stage = 1

    for speaker, message in DEMO_CONVERSATION:
        # Analyze stage before each turn
        stage_info = analyze_stage(conversation_history, current_stage)
        if stage_info["stage_changed"]:
            current_stage = stage_info["recommended_stage"]
            print(f"  [Stage -> {current_stage}: {CONVERSATION_STAGES[str(current_stage)][:50]}...]")
            print()

        if speaker == "User":
            print(f"  You: {message}")
        else:
            print(f"  {agent_name}: {message}")

        conversation_history += f"{speaker if speaker == 'User' else agent_name}: {message}\n"
        print()

    print("  [Conversation complete]\n")

    # Score it
    print_score(conversation_history, agent_name)

    # Also run stage analyzer on the final state
    final_stage = analyze_stage(conversation_history, current_stage)
    print(f"  Final stage analysis: Stage {final_stage['recommended_stage']} - {final_stage['reason']}")
    print()


# ── Display ──────────────────────────────────────────────────────────────────

def print_banner(config: dict, mode: str = "live"):
    name = config["salesperson_name"]
    role = config["salesperson_role"]
    company = config["company_name"]
    print()
    print("=" * 60)
    print("  SALESGPT CONVERSATION ENGINE")
    print("=" * 60)
    print(f"  Agent:   {name}")
    print(f"  Role:    {role}")
    print(f"  Company: {company}")
    print(f"  Type:    {config['conversation_type']}")
    print(f"  Mode:    {mode}")
    print("=" * 60)
    if mode == "live":
        print("  Type your responses as the prospect.")
        print("  Commands: 'quit' to exit, 'stage' to see current stage")
    print("=" * 60)
    print()


def print_score(history: str, agent_name: str):
    result = score_conversation(history, agent_name)
    print()
    print("=" * 50)
    print("  SALES CONVERSATION SCORECARD")
    print("=" * 50)
    print(f"  Overall: {result['overall_score']}% (Grade: {result['grade']})")
    print(f"  Turns: {result['total_turns']} ({result['agent_turns']} agent, {result['user_turns']} user)")
    print("=" * 50)
    print()
    for dim, data in result["dimensions"].items():
        bar = "\u2588" * data["score"] + "\u2591" * (10 - data["score"])
        print(f"  {dim:<20} {bar} {data['score']}/10")
        print(f"  {'':20} {data['detail']}")
        print()
    if result["recommendations"]:
        print("=" * 50)
        print("  RECOMMENDATIONS")
        print("=" * 50)
        for i, rec in enumerate(result["recommendations"], 1):
            print(f"  {i}. {rec}")
        print()


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SalesGPT Interactive Conversation Runner")
    parser.add_argument("--agent", choices=["tyler", "marcus", "ryan"], default="tyler",
                        help="Sales agent to run (default: tyler)")
    parser.add_argument("--score", action="store_true", help="Score the conversation when done")
    parser.add_argument("--demo", action="store_true", help="Run demo conversation (no API key needed)")
    args = parser.parse_args()

    config = load_agent_config(args.agent)
    agent_name = config["salesperson_name"].split()[0]

    # Demo mode
    if args.demo:
        print_banner(config, mode="demo")
        run_demo(config)
        return

    # Live mode — need an LLM
    llm_config = get_llm_client()
    if not llm_config:
        print("\n  No API key found. Set one of:")
        print("    export OPENAI_API_KEY=sk-...")
        print("    export ANTHROPIC_API_KEY=sk-ant-...")
        print("\n  Or run with --demo for a demonstration:\n")
        print("    python run_salesgpt.py --demo\n")
        sys.exit(1)

    system_prompt = build_system_prompt(config)
    print_banner(config, mode="live")

    conversation_history = ""
    current_stage = 1

    # Agent opens
    stage_info = analyze_stage("", current_stage)
    print(f"  [Stage {stage_info['recommended_stage']}: {CONVERSATION_STAGES[str(stage_info['recommended_stage'])][:50]}...]")
    print()

    opening = generate_response(llm_config, system_prompt, "", stage_info, agent_name)
    print(f"  {agent_name}: {opening}")
    print()
    conversation_history += f"{agent_name}: {opening}\n"

    # Conversation loop
    while True:
        try:
            user_input = input("  You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n  [Conversation ended]")
            break

        if not user_input:
            continue

        if user_input.lower() == "quit":
            print("\n  [Conversation ended]")
            break

        if user_input.lower() == "stage":
            info = analyze_stage(conversation_history, current_stage)
            print(f"\n  [Stage {info['recommended_stage']}: {info['recommended_stage_name'][:60]}]")
            print(f"  [Reason: {info['reason']}]")
            print(f"  [Turns: {info['conversation_turns']}]\n")
            continue

        conversation_history += f"User: {user_input}\n"

        stage_info = analyze_stage(conversation_history, current_stage)
        if stage_info["stage_changed"]:
            current_stage = stage_info["recommended_stage"]
            print(f"\n  [Stage -> {current_stage}: {CONVERSATION_STAGES[str(current_stage)][:50]}...]")

        if current_stage == 8:
            response = generate_response(llm_config, system_prompt, conversation_history, stage_info, agent_name)
            print(f"\n  {agent_name}: {response}")
            conversation_history += f"{agent_name}: {response}\n"
            print("\n  [Conversation complete - Stage 8: End]")
            break

        response = generate_response(llm_config, system_prompt, conversation_history, stage_info, agent_name)
        print(f"\n  {agent_name}: {response}\n")
        conversation_history += f"{agent_name}: {response}\n"

        if "<END_OF_CALL>" in response:
            print("  [Agent ended the conversation]")
            break

    if args.score and conversation_history.strip():
        print_score(conversation_history, agent_name)


if __name__ == "__main__":
    main()

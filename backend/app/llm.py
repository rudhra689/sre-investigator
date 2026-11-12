"""Turns the evidence into a plain-language summary using Claude.

If there is no API key (or the call fails), a simple template is used instead,
so the demo never breaks.
"""
import json
import os

from dotenv import load_dotenv

load_dotenv()

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")

PROMPT = """You are an assistant for an on-call engineer during an outage.
Using ONLY the evidence below, write a short incident summary in plain language.

Format:
1. What is happening (1-2 sentences)
2. Most likely cause (1-2 sentences, name the service and the change if any)
3. Proof (3 short bullets quoting the evidence)
4. What to do now (2-3 short bullets)

Rules: do not invent facts that are not in the evidence. If confidence is below 0.6, say the cause is uncertain.

EVIDENCE (JSON):
{evidence}
"""


def fallback_summary(result: dict, similar: list[dict]) -> str:
    rc = result["root_cause"]
    lines = [
        f"Most likely cause: the **{rc['service']}** service (confidence {int(rc['confidence'] * 100)}%).",
    ]
    if rc["deployment"]:
        d = rc["deployment"]
        lines.append(f"It was changed shortly before the problems began: {d['version']} - {d['description']}.")
    else:
        lines.append("No recent deployment was found, so this looks like a resource or infrastructure problem.")
    if rc["logs"]:
        lines.append(f"Top log evidence: \"{rc['logs'][0]['message']}\" ({rc['logs'][0]['count']} times).")
    if similar:
        lines.append(f"Suggested fix from a similar past incident: {similar[0]['fix']}")
    return "\n\n".join(lines)


def summarize(result: dict, similar: list[dict]) -> dict:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return {"text": fallback_summary(result, similar), "source": "template (no API key set)"}

    evidence = {
        "stats": result["stats"],
        "root_cause": result["root_cause"],
        "ranking": result["ranking"],
        "similar_past_incidents": similar,
    }
    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=key)
        msg = client.messages.create(
            model=MODEL,
            max_tokens=700,
            messages=[
                {"role": "user", "content": PROMPT.format(evidence=json.dumps(evidence, default=str, indent=2))}
            ],
        )
        return {"text": msg.content[0].text, "source": f"Claude ({MODEL})"}
    except Exception as e:  # never let the AI call break the investigation
        return {
            "text": fallback_summary(result, similar),
            "source": f"template (Claude call failed: {type(e).__name__})",
        }
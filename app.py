import json
import os
import re
from pathlib import Path

import requests
from flask import Flask, render_template, jsonify, request, abort

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
PERSONAS_PATH = BASE_DIR / "personas.json"

VAPI_PUBLIC_KEY = os.environ.get("VAPI_PUBLIC_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


def load_data():
    with open(PERSONAS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_persona(persona_id):
    data = load_data()
    for p in data["personas"]:
        if p["id"] == persona_id:
            return p, data
    return None, data


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    data = load_data()
    return render_template(
        "index.html",
        personas=data["personas"],
        vapi_configured=bool(VAPI_PUBLIC_KEY),
    )


@app.route("/practice/<persona_id>")
def practice(persona_id):
    persona, data = get_persona(persona_id)
    if not persona:
        abort(404)
    assistant_id = os.environ.get(f"VAPI_ASSISTANT_ID_{persona_id.upper().replace('-', '_')}", "")
    return render_template(
        "practice.html",
        persona=persona,
        rubric=data["global_rubric"],
        vapi_public_key=VAPI_PUBLIC_KEY,
        vapi_assistant_id=assistant_id,
    )


# ---------------------------------------------------------------------------
# Evaluation API
# ---------------------------------------------------------------------------

def format_transcript(messages):
    lines = []
    for m in messages:
        role = (m.get("role") or "").strip().lower()
        text = (m.get("text") or m.get("content") or "").strip()
        if not text:
            continue
        speaker = "Dispatcher (trainee)" if role in ("user",) else "Customer (AI persona)"
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


def build_eval_prompt(persona, rubric, transcript_text, company_context):
    criteria_lines = "\n".join(
        f'- id: "{c["id"]}" | weight: {c["weight"]} | criterion: {c["label"]}' for c in rubric
    )
    must_hits = "\n".join(f"- {m}" for m in persona.get("must_hits", []))

    return f"""You are grading a customer-service phone-call ROLEPLAY used to train new dispatchers at a pool leak repair company.

COMPANY CONTEXT:
{company_context}

PERSONA THE TRAINEE WAS PRACTICING WITH:
Name: {persona['name']}
Mood/personality: {persona['mood']}
Scenario: {persona['headline']}

SCENARIO-SPECIFIC THINGS A STRONG DISPATCHER SHOULD DO:
{must_hits}

GENERAL RUBRIC (score every item):
{criteria_lines}

CALL TRANSCRIPT (the trainee dispatcher is labeled "Dispatcher (trainee)"; the AI customer persona is labeled "Customer (AI persona)"):
---
{transcript_text}
---

Grade the DISPATCHER's performance only (not the AI customer). Respond with ONLY valid JSON, no markdown fences, no commentary outside the JSON, matching this exact schema:

{{
  "overall_score": <integer 0-100>,
  "letter_grade": "<A/B/C/D/F>",
  "summary": "<2-3 sentence overall summary of performance, written directly to the trainee>",
  "criteria": [
    {{"id": "<criterion id from rubric>", "label": "<criterion label>", "status": "<met|partial|not_met>", "comment": "<1 sentence, specific to what happened in this call>"}}
  ],
  "strengths": ["<specific strength 1>", "<specific strength 2>"],
  "improvements": ["<specific, actionable improvement 1>", "<specific, actionable improvement 2>"]
}}

Be fair but rigorous, like a real call-quality coach. Cite specific things the dispatcher said or failed to ask about. If the transcript is very short or the trainee hung up early, reflect that honestly in the score."""


def call_anthropic(prompt):
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": prompt}],
    }
    resp = requests.post(ANTHROPIC_API_URL, headers=headers, json=body, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    text_parts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    raw = "".join(text_parts).strip()
    raw = re.sub(r"^```(json)?", "", raw.strip())
    raw = re.sub(r"```$", "", raw.strip())
    return json.loads(raw)


def fallback_rule_based_eval(persona, rubric, transcript_text):
    """Very rough keyword-based scoring used only if no ANTHROPIC_API_KEY is set,
    so the app still works end-to-end for a demo without an LLM key."""
    lower = transcript_text.lower()
    dispatcher_lines = [
        l for l in transcript_text.split("\n") if l.startswith("Dispatcher (trainee):")
    ]
    dispatcher_text = " ".join(dispatcher_lines).lower()

    checks = {
        "greeting": any(w in dispatcher_text for w in ["hi", "hello", "thanks for calling", "mr pool"]),
        "identify": any(w in dispatcher_text for w in ["address", "phone number", "your name", "can i get"]),
        "diagnose": any(w in dispatcher_text for w in ["how much water", "how long", "pool type", "gunite", "vinyl", "fiberglass", "equipment pad", "leak"]),
        "urgency": any(w in dispatcher_text for w in ["emergency", "urgent", "right away", "today", "as soon as"]),
        "pricing": any(w in dispatcher_text for w in ["fee", "cost", "price", "credited", "diagnostic"]),
        "empathy": any(w in dispatcher_text for w in ["understand", "sorry", "i hear you", "i know that's frustrating", "no worries"]),
        "booking": any(w in dispatcher_text for w in ["schedule", "appointment", "book", "available", "tomorrow", "time works"]),
        "recap": any(w in dispatcher_text for w in ["just to confirm", "to recap", "so that's", "let me confirm"]),
        "no_overpromise": True,
    }

    criteria = []
    total_weight = 0
    earned_weight = 0
    for c in rubric:
        met = checks.get(c["id"], False)
        status = "met" if met else "not_met"
        criteria.append({
            "id": c["id"],
            "label": c["label"],
            "status": status,
            "comment": "Detected relevant language in the call." if met else "Didn't clearly detect this in the transcript — review the call.",
        })
        total_weight += c["weight"]
        earned_weight += c["weight"] if met else 0

    overall = round((earned_weight / total_weight) * 100) if total_weight else 0
    grade = "A" if overall >= 90 else "B" if overall >= 80 else "C" if overall >= 70 else "D" if overall >= 60 else "F"

    return {
        "overall_score": overall,
        "letter_grade": grade,
        "summary": (
            "This is an automated keyword-based estimate because no ANTHROPIC_API_KEY is configured "
            "on the server. Set ANTHROPIC_API_KEY in your environment for real AI-graded feedback."
        ),
        "criteria": criteria,
        "strengths": ["(Set ANTHROPIC_API_KEY for detailed, specific strengths.)"],
        "improvements": ["(Set ANTHROPIC_API_KEY for detailed, specific improvement suggestions.)"],
        "fallback": True,
    }


@app.route("/api/evaluate", methods=["POST"])
def api_evaluate():
    payload = request.get_json(force=True, silent=True) or {}
    persona_id = payload.get("persona_id")
    messages = payload.get("transcript", [])

    persona, data = get_persona(persona_id)
    if not persona:
        return jsonify({"error": "unknown persona_id"}), 400

    transcript_text = format_transcript(messages)
    if not transcript_text.strip():
        return jsonify({"error": "empty transcript"}), 400

    rubric = data["global_rubric"]

    if not ANTHROPIC_API_KEY:
        result = fallback_rule_based_eval(persona, rubric, transcript_text)
        return jsonify(result)

    try:
        prompt = build_eval_prompt(persona, rubric, transcript_text, data["company_context"])
        result = call_anthropic(prompt)
        result["fallback"] = False
        return jsonify(result)
    except Exception as e:
        app.logger.exception("Evaluation via Anthropic API failed, using fallback")
        result = fallback_rule_based_eval(persona, rubric, transcript_text)
        result["error_detail"] = str(e)
        return jsonify(result)


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

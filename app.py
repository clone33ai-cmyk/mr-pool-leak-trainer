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
    numbered_items = "\n".join(
        f"{i+1}. {c['label']} ({c['max_points']} pts) [id: \"{c['id']}\"]"
        for i, c in enumerate(rubric)
    )
    max_total = sum(c["max_points"] for c in rubric)
    example_results = ",\n    ".join(
        '{ "id": "%s", "item": "%s", "status": "hit", "points": %d, "maxPoints": %d, "note": "<one sentence, specific to this call>" }'
        % (c["id"], c["label"], c["max_points"], c["max_points"])
        for c in rubric[:2]
    )

    return f"""You are evaluating a dispatcher trainee for Mr Pool Leak Repair on a practice call.

COMPANY CONTEXT:
{company_context}

The trainee was practicing with this persona (grade the dispatcher's handling of the customer, but score the checklist below strictly on what was actually said, regardless of persona mood):
Name: {persona['name']} | Mood: {persona['mood']} | Scenario: {persona['headline']}

Score these {len(rubric)} items based on the transcript. This is the required script for every call — score each item strictly on whether it was actually said, not on effort or tone alone:
{numbered_items}

CALL TRANSCRIPT (the trainee dispatcher is labeled "Dispatcher (trainee)"; the AI customer persona is labeled "Customer (AI persona)"):
---
{transcript_text}
---

IMPORTANT: Return ONLY a raw JSON object. No markdown. No backticks. No explanation before or after. Use this exact schema:

{{
  "rubricResults": [
    {example_results},
    ...
  ],
  "totalScore": <integer 0-{max_total}, the sum of "points" across all rubricResults>,
  "coachingNotes": ["<specific, actionable coaching note>", "..."],
  "recommendation": "<one of: READY | NEEDS PRACTICE | NOT READY>",
  "summary": "<2-3 sentence overall summary written directly to the trainee>"
}}

Rules:
- Include all {len(rubric)} rubric items in "rubricResults", each with its own "id" (use the id given above), "item" (the label), "status" ("hit" | "partial" | "missed"), "points" (0 to maxPoints, "partial" should usually be about half credit), "maxPoints", and a one-sentence "note" citing what was or wasn't said.
- "totalScore" must equal the sum of all "points".
- Be fair but rigorous, like a real call-quality coach reviewing against the required script. If the call ended early or the trainee never got to a topic, mark it "missed" and say so plainly."""


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


FALLBACK_KEYWORDS = {
    "greeting": ["thanks for calling", "thank you for calling", "mr pool leak", "this is", "my name is"],
    "test_coverage": ["underground plumbing", "the shell", "equipment pad", "seals", "plumbing", "pad"],
    "duration": ["1 to 3 hours", "1-3 hours", "one to three hours", "couple hours", "few hours"],
    "pricing": ["$375", "375 dollars", "three seventy five", "three hundred and seventy five"],
    "no_home_required": ["don't have to be home", "do not have to be home", "backyard access", "don't need to be home"],
    "report_included": ["leak report", "full report", "diagnosis"],
    "estimates_included": ["repair estimate", "estimates are included", "estimate for the repair"],
    "warranty": ["warranty", "3-year", "three year", "lifetime"],
    "questions": ["any questions", "questions for me", "does that make sense", "questions so far"],
    "text_offer": ["send you a text", "text you", "shoot you a text", "text to collect"],
    "prepayment": ["prepayment", "pay ahead", "cancel anytime", "full refund", "prepay"],
    "tone": [],  # judged holistically below, not by keyword
}


def fallback_rule_based_eval(persona, rubric, transcript_text):
    """Very rough keyword-based scoring used only if no ANTHROPIC_API_KEY is set,
    so the app still works end-to-end for a demo without an LLM key."""
    dispatcher_lines = [
        l for l in transcript_text.split("\n") if l.startswith("Dispatcher (trainee):")
    ]
    dispatcher_text = " ".join(dispatcher_lines).lower()
    dispatcher_word_count = len(dispatcher_text.split())

    rubric_results = []
    total_score = 0
    for c in rubric:
        if c["id"] == "tone":
            # Rough proxy: did the dispatcher say enough to judge tone at all.
            hit = dispatcher_word_count >= 25
            status = "hit" if hit else "missed"
            points = c["max_points"] if hit else 0
            note = (
                "Enough of the call was captured to sound professional and engaged."
                if hit
                else "Too little dispatcher speech was captured to judge tone."
            )
        else:
            keywords = FALLBACK_KEYWORDS.get(c["id"], [])
            hit = any(k in dispatcher_text for k in keywords)
            status = "hit" if hit else "missed"
            points = c["max_points"] if hit else 0
            note = (
                "Detected matching language in the call."
                if hit
                else "Didn't clearly detect this in the transcript — review the call."
            )
        rubric_results.append({
            "id": c["id"],
            "item": c["label"],
            "status": status,
            "points": points,
            "maxPoints": c["max_points"],
            "note": note,
        })
        total_score += points

    max_total = sum(c["max_points"] for c in rubric)
    pct = (total_score / max_total) if max_total else 0
    recommendation = "READY" if pct >= 0.85 else "NEEDS PRACTICE" if pct >= 0.5 else "NOT READY"

    return {
        "rubricResults": rubric_results,
        "totalScore": total_score,
        "coachingNotes": [
            "This is an automated keyword-based estimate because no ANTHROPIC_API_KEY is configured on the server.",
            "Set ANTHROPIC_API_KEY in your environment for real, specific AI-graded coaching notes.",
        ],
        "recommendation": recommendation,
        "summary": (
            "This is an automated keyword-based estimate, not full AI grading. "
            "Set ANTHROPIC_API_KEY on the server for detailed, accurate feedback."
        ),
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
        # basic shape safety net in case the model drifts from the schema
        result.setdefault("rubricResults", [])
        result.setdefault("coachingNotes", [])
        result.setdefault("recommendation", "NEEDS PRACTICE")
        result.setdefault("summary", "")
        if "totalScore" not in result:
            result["totalScore"] = sum(r.get("points", 0) for r in result["rubricResults"])
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

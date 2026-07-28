# Mr Pool Leak Repair — Dispatcher Trainer

A small Flask web app for training new dispatchers. Trainees pick a customer **persona** (different mood, different leak scenario), have a live voice call with it via **VAPI**, and get an **AI-graded scorecard** afterward.

## How it works

1. `/` — grid of personas to choose from
2. `/practice/<persona_id>` — call screen: click **Start call**, talk through the mic (VAPI Web SDK), see a live transcript
3. When the call ends, the transcript is sent to `/api/evaluate`, which asks Claude to grade the dispatcher against a rubric and returns a scorecard (overall score, per-criterion pass/fail, strengths, and things to improve)
4. If no `ANTHROPIC_API_KEY` is set, a rough keyword-based grader is used instead so the app still works end-to-end for a demo

Personas, their VAPI system prompts, and the scoring rubric all live in **`personas.json`** — edit that file to add/change scenarios without touching code.

## 1. Create the VAPI assistants

Open **`VAPI_PROMPTS.md`** — it has one ready-to-paste system prompt + first message per persona (6 total). For each one:

1. VAPI Dashboard → Assistants → Create Assistant
2. Paste the system prompt and first message
3. Pick any natural-sounding voice
4. Save and copy the Assistant ID

You'll plug these IDs into environment variables (see below).

## 2. Configure environment variables

Copy `.env.example` to `.env` locally, or set these directly in Railway:

| Variable | Required | Notes |
|---|---|---|
| `VAPI_PUBLIC_KEY` | Yes | VAPI Dashboard → API Keys → **Public** key (safe for the browser) |
| `VAPI_ASSISTANT_ID_<PERSONA>` | Yes, per persona | One per persona, see `.env.example` for exact names |
| `ANTHROPIC_API_KEY` | Recommended | Powers the real AI call grader. Without it, a basic keyword-based grader is used |
| `ANTHROPIC_MODEL` | No | Defaults to `claude-sonnet-5` |
| `PORT` | No | Set automatically by Railway |

## 3. Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export $(cat .env | xargs)   # or use direnv / python-dotenv
python app.py
```

Visit `http://localhost:5000`.

## 4. Deploy to Railway

1. Push this folder to a new GitHub repo
2. Railway → New Project → Deploy from GitHub repo
3. Railway auto-detects Python via Nixpacks and uses the `Procfile` (`gunicorn app:app`)
4. Add the environment variables from step 2 in Railway's **Variables** tab
5. Deploy — Railway gives you a public URL

No database is used, so there's nothing else to provision.

## Project structure

```
app.py                  Flask app: pages + /api/evaluate grading endpoint
personas.json           Personas, VAPI prompts, and the scoring rubric (edit this to change scenarios)
VAPI_PROMPTS.md          Copy-paste prompts for creating the VAPI assistants
templates/               Jinja templates (index, practice)
static/css/style.css     Styling
static/js/main.js        Drives the call (start/stop, live transcript, calls the evaluator)
static/js/vapi-web-bundle.js   Pre-bundled official @vapi-ai/web SDK (so no npm build step is needed to deploy)
```

## Adding a new persona

Add an entry to the `personas` array in `personas.json` (copy an existing one as a template), then:
1. Add its prompt to a new VAPI assistant (or regenerate `VAPI_PROMPTS.md`'s pattern manually)
2. Add its `VAPI_ASSISTANT_ID_<ID>` environment variable
3. No code changes needed — the persona grid and practice page are generated from this file

## Notes / things you may want to extend

- No login or history is stored — each session is stateless. Add a database if you want to track trainee progress over time.
- The evaluator prompt and rubric are shared across personas (`global_rubric` in `personas.json`) with persona-specific "must hits" layered in — tune the wording there to match your actual call script/SOP.
- `vapi-web-bundle.js` was built once with `esbuild` from the official `@vapi-ai/web` npm package. If VAPI ships SDK updates you want, rebuild it with:
  ```bash
  npm install @vapi-ai/web esbuild
  npx esbuild entry.js --bundle --minify --format=iife --outfile=static/js/vapi-web-bundle.js
  # entry.js: import Vapi from "@vapi-ai/web"; window.Vapi = Vapi;
  ```

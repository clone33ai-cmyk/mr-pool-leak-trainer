(function () {
  const personaId = window.__PERSONA_ID__;
  const publicKey = window.__VAPI_PUBLIC_KEY__;
  const assistantId = window.__VAPI_ASSISTANT_ID__;

  const callBtn = document.getElementById("call-btn");
  const statusEl = document.getElementById("call-status");
  const transcriptEl = document.getElementById("transcript");
  const resultsPanel = document.getElementById("results");
  const resultsLoading = document.getElementById("results-loading");
  const resultsBody = document.getElementById("results-body");

  if (!callBtn) return;

  let vapi = null;
  let callActive = false;
  let transcript = []; // { role: 'user' | 'assistant', text }
  let lastRole = null;
  let lastBubble = null;

  function setStatus(state, label) {
    statusEl.className = "call-status " + state;
    statusEl.textContent = label;
  }

  function clearTranscript() {
    transcript = [];
    lastRole = null;
    lastBubble = null;
    transcriptEl.innerHTML = "";
  }

  function appendTranscriptChunk(role, text) {
    if (!text) return;
    // role: 'user' = the trainee dispatcher speaking, 'assistant' = the AI persona (customer)
    if (role === lastRole && lastBubble) {
      lastBubble.querySelector(".bubble-text").textContent += " " + text;
      transcript[transcript.length - 1].text += " " + text;
    } else {
      const bubble = document.createElement("div");
      bubble.className = "bubble " + (role === "user" ? "dispatcher" : "customer");
      const speakerLabel = role === "user" ? "You (dispatcher)" : "Caller";
      bubble.innerHTML =
        '<span class="speaker">' + speakerLabel + '</span><span class="bubble-text"></span>';
      bubble.querySelector(".bubble-text").textContent = text;
      transcriptEl.appendChild(bubble);
      lastBubble = bubble;
      lastRole = role;
      transcript.push({ role, text });
    }
    transcriptEl.scrollTop = transcriptEl.scrollHeight;
  }

  function resetCallUi() {
    callBtn.classList.remove("hangup");
    callBtn.innerHTML = '<span class="dot"></span> Start call';
    callBtn.disabled = !(publicKey && assistantId);
  }

  function initVapi() {
    if (!window.Vapi) {
      console.error("Vapi SDK failed to load.");
      return null;
    }
    const instance = new window.Vapi(publicKey);

    instance.on("call-start", () => {
      callActive = true;
      setStatus("active", "Call in progress");
      callBtn.classList.add("hangup");
      callBtn.innerHTML = '<span class="dot"></span> Hang up';
      resultsPanel.classList.add("hidden");
      clearTranscript();
      const empty = document.querySelector(".transcript-empty");
      if (empty) empty.remove();
    });

    instance.on("call-end", () => {
      callActive = false;
      setStatus("ended", "Call ended");
      resetCallUi();
      if (transcript.length > 0) {
        runEvaluation();
      }
    });

    instance.on("message", (message) => {
      if (message && message.type === "transcript" && message.transcriptType === "final") {
        appendTranscriptChunk(message.role, message.transcript);
      }
    });

    instance.on("error", (e) => {
      console.error("Vapi error:", e);
      setStatus("idle", "Error — see console");
      resetCallUi();
    });

    return instance;
  }

  callBtn.addEventListener("click", () => {
    if (!publicKey || !assistantId) return;

    if (!vapi) {
      vapi = initVapi();
      if (!vapi) return;
    }

    if (callActive) {
      vapi.stop();
      return;
    }

    setStatus("connecting", "Connecting…");
    callBtn.disabled = true;
    vapi.start(assistantId).finally(() => {
      callBtn.disabled = false;
    });
  });

  // -------------------------------------------------------------------
  // Evaluation
  // -------------------------------------------------------------------

  function statusIcon(status) {
    if (status === "met") return "✓";
    if (status === "partial") return "~";
    return "✕";
  }

  function scoreColor(score) {
    if (score >= 85) return "#6fd08c";
    if (score >= 70) return "#f2b84b";
    return "#ff6b4a";
  }

  function renderResults(data) {
    resultsLoading.classList.add("hidden");
    const score = data.overall_score ?? 0;
    const color = scoreColor(score);
    const circumference = 2 * Math.PI * 46;
    const offset = circumference - (score / 100) * circumference;

    const fallbackBanner = data.fallback
      ? '<div class="fallback-banner">This is a rough keyword-based estimate — set <code>ANTHROPIC_API_KEY</code> on the server for real AI-graded feedback.</div>'
      : "";

    const criteriaHtml = (data.criteria || [])
      .map(
        (c) => `
        <div class="criteria-item">
          <span class="status-icon status-${c.status}">${statusIcon(c.status)}</span>
          <div>
            <div class="criteria-label">${escapeHtml(c.label)}</div>
            <div class="criteria-comment">${escapeHtml(c.comment || "")}</div>
          </div>
        </div>`
      )
      .join("");

    const strengthsHtml = (data.strengths || []).map((s) => `<li>${escapeHtml(s)}</li>`).join("");
    const improvementsHtml = (data.improvements || [])
      .map((s) => `<li>${escapeHtml(s)}</li>`)
      .join("");

    resultsBody.innerHTML = `
      ${fallbackBanner}
      <div class="score-head">
        <div class="score-ring">
          <svg width="108" height="108" viewBox="0 0 108 108">
            <circle cx="54" cy="54" r="46" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="10" />
            <circle cx="54" cy="54" r="46" fill="none" stroke="${color}" stroke-width="10"
              stroke-dasharray="${circumference}" stroke-dashoffset="${offset}" stroke-linecap="round" />
          </svg>
          <div class="score-ring-num">
            <span class="n" style="color:${color}">${score}</span>
            <span class="l">${escapeHtml(data.letter_grade || "")}</span>
          </div>
        </div>
        <p class="summary-text">${escapeHtml(data.summary || "")}</p>
      </div>
      <div class="result-columns">
        <div>
          <h3 style="font-size:0.82rem;text-transform:uppercase;letter-spacing:.05em;color:var(--text-muted);margin-bottom:10px;">Rubric breakdown</h3>
          ${criteriaHtml}
        </div>
        <div>
          <div class="side-list strengths">
            <h3>What went well</h3>
            <ul>${strengthsHtml}</ul>
          </div>
          <div class="side-list improvements">
            <h3>Focus on next time</h3>
            <ul>${improvementsHtml}</ul>
          </div>
        </div>
      </div>
      <button class="retry-btn" onclick="location.reload()">Practice again</button>
    `;
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
  }

  function runEvaluation() {
    resultsPanel.classList.remove("hidden");
    resultsLoading.classList.remove("hidden");
    resultsBody.innerHTML = "";
    resultsPanel.scrollIntoView({ behavior: "smooth", block: "start" });

    fetch("/api/evaluate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ persona_id: personaId, transcript }),
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.error) {
          resultsLoading.classList.add("hidden");
          resultsBody.innerHTML = `<p style="color:var(--coral)">Couldn't grade this call: ${escapeHtml(
            data.error
          )}</p>`;
          return;
        }
        renderResults(data);
      })
      .catch((err) => {
        resultsLoading.classList.add("hidden");
        resultsBody.innerHTML = `<p style="color:var(--coral)">Couldn't reach the evaluator: ${escapeHtml(
          err.message
        )}</p>`;
      });
  }
})();

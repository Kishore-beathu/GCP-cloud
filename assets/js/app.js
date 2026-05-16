/* CSV Academy — Single-page learning tool.
   Plain-vanilla JS. State: hash-based routing + localStorage progress. */

(function () {
  "use strict";

  const STORAGE_KEY = "csv-academy-state-v1";

  /* ---------- state ---------- */
  const state = loadState();

  function loadState() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) return JSON.parse(raw);
    } catch (e) { /* ignore */ }
    return { visited: {}, quizScores: {} };
  }
  function saveState() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch (e) { /* ignore */ }
    updateProgressBadge();
  }
  function markVisited(key) {
    if (!state.visited[key]) {
      state.visited[key] = true;
      saveState();
    }
  }

  /* ---------- routing ---------- */
  const ROUTES = {
    home:     renderHome,
    gamp5:    renderGamp5Index,
    "gamp5-mod": renderGamp5Module,   // expects :id
    annex11:  renderAnnex11,
    "annex11-sec": renderAnnex11Section, // expects :ref
    part11:   renderPart11,
    "part11-sec": renderPart11Section,   // expects :ref
    compare:  renderCompare,
    quiz:     renderQuizIndex,
    "quiz-run": renderQuizRun,        // expects :id
    glossary: renderGlossary,
  };

  function go(route, param) {
    const hash = param != null ? `#/${route}/${encodeURIComponent(param)}` : `#/${route}`;
    if (location.hash !== hash) location.hash = hash;
    else router();
  }

  function router() {
    const raw = (location.hash || "#/home").replace(/^#\//, "");
    const [route, param] = raw.split("/");
    const fn = ROUTES[route] || (param ? ROUTES[route + "-" + paramKind(route)] : null);
    const main = document.getElementById("main");
    main.scrollIntoView({ block: "start", behavior: "instant" });
    window.scrollTo({ top: 0, behavior: "instant" });

    if (route === "gamp5" && param) return renderGamp5Module(decodeURIComponent(param));
    if (route === "annex11" && param) return renderAnnex11Section(decodeURIComponent(param));
    if (route === "part11" && param) return renderPart11Section(decodeURIComponent(param));
    if (route === "quiz" && param) return renderQuizRun(decodeURIComponent(param));

    (ROUTES[route] || renderHome)();
    setActiveNav(route);
    renderSidebar(route, param);
  }

  window.addEventListener("hashchange", router);

  /* ---------- chrome ---------- */
  function setActiveNav(route) {
    document.querySelectorAll(".nav-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.route === route);
    });
  }

  document.querySelectorAll(".nav-btn").forEach((b) => {
    b.addEventListener("click", () => go(b.dataset.route));
  });

  function updateProgressBadge() {
    const totals = totalProgress();
    const el = document.getElementById("progressBadge");
    if (el) el.textContent = `Progress: ${totals.percent}% (${totals.done}/${totals.total})`;
  }

  function totalProgress() {
    const mods = window.GAMP5_MODULES.length;
    const annexCount = window.ANNEX11.sections.length;
    const partCount = window.PART11.subparts.reduce((a, s) => a + s.sections.length, 0);
    const quizCount = Object.keys(window.QUIZZES).length;
    const total = mods + annexCount + partCount + quizCount;
    let done = 0;
    Object.keys(state.visited).forEach((k) => {
      if (state.visited[k]) done += 1;
    });
    done = Math.min(done, total);
    return { done, total, percent: total ? Math.round((done / total) * 100) : 0 };
  }

  /* ---------- sidebar ---------- */
  function renderSidebar(route, param) {
    const sb = document.getElementById("sidebar");
    let html = "";

    if (route === "gamp5" || route === "home" || !route) {
      html += `<div class="side-group"><h3>GAMP 5 modules</h3>`;
      html += window.GAMP5_MODULES.map((m) => sideLink(m.title, () => `#/gamp5/${m.id}`, param === m.id, state.visited["gamp5:" + m.id])).join("");
      html += `</div>`;
    } else if (route === "annex11") {
      html += `<div class="side-group"><h3>Annex 11 sections</h3>`;
      html += window.ANNEX11.sections.map((s) =>
        sideLink(`§${s.ref} — ${s.title}`, () => `#/annex11/${s.ref}`, param === s.ref, state.visited["annex11:" + s.ref])
      ).join("");
      html += `</div>`;
    } else if (route === "part11") {
      html += `<div class="side-group"><h3>Part 11 sections</h3>`;
      window.PART11.subparts.forEach((sub) => {
        html += `<h3 style="margin-top:14px">${escapeHtml(sub.title)}</h3>`;
        html += sub.sections.map((s) =>
          sideLink(`${s.ref} — ${s.title}`, () => `#/part11/${encodeURIComponent(s.ref)}`, param === s.ref, state.visited["part11:" + s.ref])
        ).join("");
      });
      html += `</div>`;
    } else if (route === "quiz") {
      html += `<div class="side-group"><h3>Quiz banks</h3>`;
      html += Object.keys(window.QUIZZES).map((k) =>
        sideLink(window.QUIZZES[k].title, () => `#/quiz/${k}`, param === k, state.visited["quiz:" + k])
      ).join("");
      html += `</div>`;
    } else if (route === "glossary") {
      html += `<div class="side-group"><h3>Jump to letter</h3>`;
      const letters = Array.from(new Set(window.GLOSSARY.map((g) => g.term[0].toUpperCase()))).sort();
      html += letters.map((L) =>
        `<a class="side-link" href="#gl-${L}">${L}</a>`
      ).join("");
      html += `</div>`;
    } else if (route === "compare") {
      html += `<div class="side-group"><h3>Comparison</h3>
        <a class="side-link active">Topic × Standard</a></div>`;
    } else {
      html += `<div class="side-group"><h3>Navigation</h3>
        <a class="side-link" href="#/gamp5">GAMP 5</a>
        <a class="side-link" href="#/annex11">Annex 11</a>
        <a class="side-link" href="#/part11">21 CFR Part 11</a>
        <a class="side-link" href="#/compare">Comparison</a>
        <a class="side-link" href="#/quiz">Quizzes</a>
        <a class="side-link" href="#/glossary">Glossary</a>
      </div>`;
    }
    sb.innerHTML = html;
  }

  function sideLink(label, hrefFn, active, visited) {
    return `<a class="side-link ${active ? "active" : ""}" href="${hrefFn()}">
      <span>${escapeHtml(label)}</span>
      ${visited ? `<span class="check">&check;</span>` : ""}
    </a>`;
  }

  /* ---------- views ---------- */

  function renderHome() {
    const main = document.getElementById("main");
    const p = totalProgress();
    main.innerHTML = `
      <span class="kicker">CSV Academy</span>
      <h1>Learn Computer System Validation</h1>
      <p class="lead">An interactive walkthrough of <strong>ISPE GAMP 5 (2nd edition)</strong> with the
      regulatory expectations from <strong>EU GMP Annex 11</strong> and <strong>21 CFR Part 11</strong>.
      Build the mental model, then test yourself with topic quizzes.</p>

      <div class="card-grid">
        <a class="card clickable" href="#/gamp5">
          <span class="badge cat4">GAMP 5</span>
          <div class="card-title">20 GAMP 5 modules</div>
          <div class="card-body">From categorisation through Agile, cloud, AI/ML and decommissioning.</div>
        </a>
        <a class="card clickable" href="#/annex11">
          <span class="badge regs">Annex 11</span>
          <div class="card-title">EU GMP Annex 11</div>
          <div class="card-body">All 17 numbered sections plus the Principle, in study-ready form.</div>
        </a>
        <a class="card clickable" href="#/part11">
          <span class="badge regs">21 CFR Part 11</span>
          <div class="card-title">FDA Electronic Records & Signatures</div>
          <div class="card-body">Subparts A, B, C — controls, signatures and ID/password requirements.</div>
        </a>
        <a class="card clickable" href="#/compare">
          <span class="badge cat3">Compare</span>
          <div class="card-title">Cross-reference matrix</div>
          <div class="card-body">Each CSV topic mapped across the three frameworks at a glance.</div>
        </a>
        <a class="card clickable" href="#/quiz">
          <span class="badge cat5">Quizzes</span>
          <div class="card-title">${countQuestions()} questions across 4 banks</div>
          <div class="card-body">GAMP 5, Annex 11, Part 11 and mixed real-world scenarios.</div>
        </a>
        <a class="card clickable" href="#/glossary">
          <span class="badge cat1">Glossary</span>
          <div class="card-title">${window.GLOSSARY.length} CSV terms</div>
          <div class="card-body">From ALCOA+ to V-model. Searchable and tagged.</div>
        </a>
      </div>

      <h2>Your progress</h2>
      <div class="callout"><strong>${p.percent}%</strong> &mdash; ${p.done} of ${p.total} sections / quizzes visited.
      Progress is saved in your browser only.</div>

      <h2>Suggested learning path</h2>
      <ol>
        <li>Read <a href="#/gamp5/intro">GAMP 5 — Introduction</a> and <a href="#/gamp5/gxp">GxP &amp; ALCOA+</a>.</li>
        <li>Cover <a href="#/gamp5/qrm">Quality Risk Management</a> then <a href="#/gamp5/categories">Software Categories</a>.</li>
        <li>Work through the life-cycle modules (5 – 11).</li>
        <li>Read each <a href="#/annex11">Annex 11 section</a> and each <a href="#/part11">Part 11 section</a>.</li>
        <li>Use the <a href="#/compare">Comparison matrix</a> to consolidate.</li>
        <li>Take the <a href="#/quiz">quizzes</a>; aim for 80%+ on each bank.</li>
      </ol>
    `;
    markVisited("home");
  }

  function renderGamp5Index() {
    const main = document.getElementById("main");
    main.innerHTML = `
      <span class="kicker">ISPE GAMP 5</span>
      <h1>${escapeHtml(window.GAMP5_MODULES.length)} GAMP 5 modules</h1>
      <p class="lead">Risk-based life-cycle approach to compliant GxP computerised systems. Updated to reflect
      the 2nd edition (2022).</p>
      <div class="card-grid">
        ${window.GAMP5_MODULES.map((m) => `
          <a class="card clickable" href="#/gamp5/${m.id}">
            <div class="card-title">${escapeHtml(m.title)}</div>
            <div class="card-body">${escapeHtml(m.summary)}</div>
            <div class="card-meta">${state.visited["gamp5:" + m.id] ? "&check; Visited" : "Not yet visited"}</div>
          </a>
        `).join("")}
      </div>
    `;
  }

  function renderGamp5Module(id) {
    const idx = window.GAMP5_MODULES.findIndex((m) => m.id === id);
    if (idx < 0) return go("gamp5");
    const m = window.GAMP5_MODULES[idx];
    const prev = window.GAMP5_MODULES[idx - 1];
    const next = window.GAMP5_MODULES[idx + 1];
    const main = document.getElementById("main");
    main.innerHTML = `
      <div class="crumbs"><a href="#/gamp5">GAMP 5</a> &rsaquo; ${escapeHtml(m.title)}</div>
      <h1>${escapeHtml(m.title)}</h1>
      <p class="lead">${escapeHtml(m.summary)}</p>
      ${m.sections.map((s) => `<h2>${escapeHtml(s.h)}</h2>${s.body}`).join("")}
      ${pager(
        prev ? { href: `#/gamp5/${prev.id}`, label: prev.title, dir: "Previous" } : null,
        next ? { href: `#/gamp5/${next.id}`, label: next.title, dir: "Next" } : null
      )}
    `;
    setActiveNav("gamp5");
    markVisited("gamp5:" + id);
    renderSidebar("gamp5", id);
  }

  function renderAnnex11() {
    const main = document.getElementById("main");
    main.innerHTML = `
      <span class="kicker">EudraLex Volume 4</span>
      <h1>${escapeHtml(window.ANNEX11.title)}</h1>
      ${window.ANNEX11.intro}
      <h2>All sections</h2>
      <div class="card-grid">
        ${window.ANNEX11.sections.map((s) => `
          <a class="card clickable" href="#/annex11/${encodeURIComponent(s.ref)}">
            <span class="badge regs">§${escapeHtml(s.ref)}</span>
            <div class="card-title">${escapeHtml(s.title)}</div>
            <div class="card-body">${escapeHtml(s.summary)}</div>
          </a>
        `).join("")}
      </div>
    `;
  }

  function renderAnnex11Section(ref) {
    const sections = window.ANNEX11.sections;
    const idx = sections.findIndex((s) => s.ref === ref);
    if (idx < 0) return go("annex11");
    const s = sections[idx];
    const prev = sections[idx - 1];
    const next = sections[idx + 1];
    const main = document.getElementById("main");
    main.innerHTML = `
      <div class="crumbs"><a href="#/annex11">Annex 11</a> &rsaquo; §${escapeHtml(s.ref)} ${escapeHtml(s.title)}</div>
      <span class="badge regs">§${escapeHtml(s.ref)}</span>
      <h1>${escapeHtml(s.title)}</h1>
      <p class="lead">${escapeHtml(s.summary)}</p>
      ${s.body}
      ${pager(
        prev ? { href: `#/annex11/${encodeURIComponent(prev.ref)}`, label: `§${prev.ref} ${prev.title}`, dir: "Previous" } : null,
        next ? { href: `#/annex11/${encodeURIComponent(next.ref)}`, label: `§${next.ref} ${next.title}`, dir: "Next" } : null
      )}
    `;
    setActiveNav("annex11");
    markVisited("annex11:" + ref);
    renderSidebar("annex11", ref);
  }

  function renderPart11() {
    const main = document.getElementById("main");
    const subs = window.PART11.subparts;
    main.innerHTML = `
      <span class="kicker">FDA / 21 CFR</span>
      <h1>${escapeHtml(window.PART11.title)}</h1>
      ${window.PART11.intro}
      ${subs.map((sub) => `
        <h2>${escapeHtml(sub.title)}</h2>
        <div class="card-grid">
          ${sub.sections.map((s) => `
            <a class="card clickable" href="#/part11/${encodeURIComponent(s.ref)}">
              <span class="badge regs">${escapeHtml(s.ref)}</span>
              <div class="card-title">${escapeHtml(s.title)}</div>
            </a>
          `).join("")}
        </div>
      `).join("")}
    `;
  }

  function renderPart11Section(ref) {
    const flat = [];
    window.PART11.subparts.forEach((sub) => sub.sections.forEach((s) => flat.push({ s, subTitle: sub.title })));
    const idx = flat.findIndex((x) => x.s.ref === ref);
    if (idx < 0) return go("part11");
    const { s, subTitle } = flat[idx];
    const prev = flat[idx - 1];
    const next = flat[idx + 1];
    const main = document.getElementById("main");
    main.innerHTML = `
      <div class="crumbs"><a href="#/part11">21 CFR Part 11</a> &rsaquo; ${escapeHtml(subTitle)} &rsaquo; ${escapeHtml(s.ref)} ${escapeHtml(s.title)}</div>
      <span class="badge regs">${escapeHtml(s.ref)}</span>
      <h1>${escapeHtml(s.title)}</h1>
      ${s.body}
      ${pager(
        prev ? { href: `#/part11/${encodeURIComponent(prev.s.ref)}`, label: `${prev.s.ref} ${prev.s.title}`, dir: "Previous" } : null,
        next ? { href: `#/part11/${encodeURIComponent(next.s.ref)}`, label: `${next.s.ref} ${next.s.title}`, dir: "Next" } : null
      )}
    `;
    setActiveNav("part11");
    markVisited("part11:" + ref);
    renderSidebar("part11", ref);
  }

  function renderCompare() {
    const main = document.getElementById("main");
    main.innerHTML = `
      <span class="kicker">Cross-reference</span>
      <h1>CSV topics × standards</h1>
      <p class="lead">Use this matrix to find equivalent or related obligations between GAMP 5, Annex 11
      and 21 CFR Part 11. Click into the sidebar links from each framework to read the source text.</p>
      <div class="table-wrap"><table class="styled">
        <thead><tr>
          <th>Topic</th>
          <th>GAMP 5 (2nd ed.)</th>
          <th>EU Annex 11</th>
          <th>21 CFR Part 11</th>
        </tr></thead>
        <tbody>
          ${window.COMPARE.map((r) => `
            <tr>
              <td><strong>${r.topic}</strong></td>
              <td>${r.gamp5}</td>
              <td>${r.annex11}</td>
              <td>${r.part11}</td>
            </tr>
          `).join("")}
        </tbody>
      </table></div>
    `;
    markVisited("compare");
  }

  /* ---------- quizzes ---------- */
  function renderQuizIndex() {
    const main = document.getElementById("main");
    const banks = window.QUIZZES;
    main.innerHTML = `
      <span class="kicker">Self-assessment</span>
      <h1>Quizzes</h1>
      <p class="lead">Four banks of multiple-choice questions with explanations. Best scores are saved in your browser.</p>
      <div class="card-grid">
        ${Object.keys(banks).map((k) => {
          const b = banks[k];
          const best = state.quizScores[k];
          const bestStr = best ? `Best: ${best.score}/${best.total} (${Math.round(best.score / best.total * 100)}%)` : "Not yet attempted";
          return `
            <a class="card clickable" href="#/quiz/${k}">
              <div class="card-title">${escapeHtml(b.title)}</div>
              <div class="card-body">${b.questions.length} questions</div>
              <div class="card-meta">${bestStr}</div>
            </a>`;
        }).join("")}
      </div>
    `;
  }

  function renderQuizRun(id) {
    const bank = window.QUIZZES[id];
    if (!bank) return go("quiz");
    const qs = bank.questions;
    const userAnswers = qs.map(() => -1);

    const main = document.getElementById("main");
    main.innerHTML = `
      <div class="crumbs"><a href="#/quiz">Quizzes</a> &rsaquo; ${escapeHtml(bank.title)}</div>
      <h1>${escapeHtml(bank.title)}</h1>
      <p class="lead">${qs.length} questions. Select an answer for each, then submit at the end.</p>
      <div id="quizList"></div>
      <div style="display:flex; gap:10px; margin-top:18px;">
        <button class="btn" id="quizSubmit">Submit answers</button>
        <button class="btn ghost" id="quizReset">Reset</button>
      </div>
      <div id="quizResult"></div>
    `;
    setActiveNav("quiz");
    renderSidebar("quiz", id);

    const list = document.getElementById("quizList");
    list.innerHTML = qs.map((q, i) => `
      <div class="quiz-card" data-q="${i}">
        <div class="quiz-meta"><span>Question ${i + 1} of ${qs.length}</span></div>
        <div class="quiz-q">${escapeHtml(q.q)}</div>
        <div class="quiz-options">
          ${q.options.map((opt, oi) => `
            <button class="quiz-option" data-q="${i}" data-o="${oi}">
              ${String.fromCharCode(65 + oi)}. ${escapeHtml(opt)}
            </button>
          `).join("")}
        </div>
        <div class="quiz-explain" style="display:none"></div>
      </div>
    `).join("");

    list.querySelectorAll(".quiz-option").forEach((btn) => {
      btn.addEventListener("click", () => {
        const qi = +btn.dataset.q;
        const oi = +btn.dataset.o;
        userAnswers[qi] = oi;
        const parent = btn.parentElement;
        parent.querySelectorAll(".quiz-option").forEach((b) => b.classList.remove("selected"));
        btn.classList.add("selected");
      });
    });

    document.getElementById("quizSubmit").addEventListener("click", () => {
      let score = 0;
      qs.forEach((q, i) => {
        const card = list.querySelector(`.quiz-card[data-q="${i}"]`);
        const opts = card.querySelectorAll(".quiz-option");
        const chosen = userAnswers[i];
        opts.forEach((b, oi) => {
          b.disabled = true;
          if (oi === q.a) b.classList.add("correct");
          if (oi === chosen && oi !== q.a) b.classList.add("wrong");
        });
        if (chosen === q.a) score += 1;
        const ex = card.querySelector(".quiz-explain");
        ex.style.display = "block";
        ex.innerHTML = `<strong>Explanation:</strong> ${escapeHtml(q.explain)}`;
      });
      const pct = Math.round((score / qs.length) * 100);
      const grade = pct >= 80 ? "ok" : pct >= 60 ? "warn" : "fail";
      const summary = document.getElementById("quizResult");
      summary.innerHTML = `
        <div class="quiz-summary">
          <div class="score">${score}/${qs.length}</div>
          <p>You scored <strong>${pct}%</strong> on <strong>${escapeHtml(bank.title)}</strong>.
          <span class="badge ${grade}">${pct >= 80 ? "Pass" : pct >= 60 ? "Almost" : "Review"}</span></p>
          <p>Review the explanations above before retrying.</p>
        </div>
      `;
      const best = state.quizScores[id];
      if (!best || best.score < score) state.quizScores[id] = { score, total: qs.length };
      markVisited("quiz:" + id);
    });

    document.getElementById("quizReset").addEventListener("click", () => renderQuizRun(id));
  }

  /* ---------- glossary ---------- */
  function renderGlossary() {
    const main = document.getElementById("main");
    main.innerHTML = `
      <span class="kicker">Reference</span>
      <h1>Glossary</h1>
      <p class="lead">Search and browse common CSV terminology. Tagged by standard and topic.</p>
      <input type="search" class="gl-search" id="glSearch" placeholder="Search terms or definitions..." aria-label="Search glossary" />
      <div id="glList"></div>
    `;
    function rebuild(filter) {
      const sorted = [...window.GLOSSARY].sort((a, b) => a.term.localeCompare(b.term));
      const list = document.getElementById("glList");
      const f = (filter || "").toLowerCase().trim();
      let lastLetter = "";
      const out = [];
      sorted.forEach((g) => {
        const hay = (g.term + " " + g.def + " " + g.tags.join(" ")).toLowerCase();
        if (f && !hay.includes(f)) return;
        const L = g.term[0].toUpperCase();
        if (L !== lastLetter) {
          out.push(`<div class="gl-letter" id="gl-${L}">${L}</div>`);
          lastLetter = L;
        }
        out.push(`
          <div class="gl-term">
            <div class="gl-name">${escapeHtml(g.term)}</div>
            <div class="gl-def">${escapeHtml(g.def)}</div>
            <div class="gl-tags">${g.tags.map((t) => `<span class="gl-tag">${escapeHtml(t)}</span>`).join("")}</div>
          </div>
        `);
      });
      list.innerHTML = out.join("") || `<p class="lead">No terms match "${escapeHtml(filter)}".</p>`;
    }
    rebuild("");
    document.getElementById("glSearch").addEventListener("input", (e) => rebuild(e.target.value));
    markVisited("glossary");
  }

  /* ---------- helpers ---------- */
  function pager(prev, next) {
    return `<div class="pager">
      <a class="pager-link ${prev ? "" : "disabled"}" href="${prev ? prev.href : "#"}">
        <span class="pager-label">${prev ? prev.dir : ""}</span>
        ${prev ? escapeHtml(prev.label) : ""}
      </a>
      <a class="pager-link ${next ? "" : "disabled"}" href="${next ? next.href : "#"}" style="text-align:right">
        <span class="pager-label">${next ? next.dir : ""}</span>
        ${next ? escapeHtml(next.label) : ""}
      </a>
    </div>`;
  }

  function countQuestions() {
    return Object.values(window.QUIZZES).reduce((a, b) => a + b.questions.length, 0);
  }

  function escapeHtml(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function paramKind() { return ""; }

  /* ---------- boot ---------- */
  router();
  updateProgressBadge();
})();

"use strict";

let appData;
let countdownTimer;
let roundTab = "predictions";
let pendingRound;
let activityFilter = "ALL";

// Football-Data crest IDs for the clubs in the supplied Super 6 round.
// Unknown teams retain the accessible initials fallback inside teamBadge.
const TEAM_CRESTS = {
  "Arsenal": 57, "Aston Villa": 58, "Bournemouth": 1044, "Brentford": 402,
  "Chelsea": 61, "Everton": 62, "Fulham": 63, "Leeds": 341,
  "Man City": 65, "Newcastle": 67, "Tottenham": 73, "West Ham": 563,
};

const $ = (selector) => document.querySelector(selector);
const percent = (value) => `${(Number(value) * 100).toFixed(1)}%`;
const money = (value) => `£${Number(value).toFixed(0)}`;
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#039;", '"': "&quot;",
}[char]));
const titleCase = (value) => String(value || "").replaceAll("_", " ").toLowerCase().replace(/\b\w/g, (letter) => letter.toUpperCase());

function displayDate(iso) {
  return new Intl.DateTimeFormat("en-GB", {
    weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Europe/London", timeZoneName: "short",
  }).format(new Date(iso));
}

function localDateTime(iso) {
  const date = new Date(iso);
  const london = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/London", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(date);
  const parts = Object.fromEntries(london.map((part) => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

function shortTeam(team) {
  return team.split(" ").map((word) => word[0]).join("").slice(0, 3).toUpperCase();
}

function teamBadge(team, size = "") {
  const crest = TEAM_CRESTS[team];
  const image = crest ? `<img src="https://crests.football-data.org/${crest}.png" alt="" loading="lazy" onerror="this.hidden=true">` : "";
  return `<span class="team-badge ${size}" role="img" aria-label="${escapeHtml(team)} badge"><b>${escapeHtml(shortTeam(team))}</b>${image}</span>`;
}

function effectivePick(fixture) {
  return fixture.user_pick || fixture.strategy_pick || fixture.pick;
}

function fixtureChange(index) {
  return (appData.round.changes_since_last_visit || []).find((change) => change.fixture_index === index && change.kind === "CHANGED");
}

function leagueSummary() {
  const league = appData.league;
  const row = league.standings.find((item) => item.participant === appData.settings.player_name);
  if (!row) return `<article class="league-summary"><p class="eyebrow">Your league</p><strong>—</strong><span>Add entered picks and results to build your table.</span></article>`;
  const leader = league.standings[0];
  const probability = league.finish_first ? percent(league.finish_first.probability) : "Not estimated";
  return `<article class="league-summary"><p class="eyebrow">Your league</p><strong>${row.rank}${row.rank === 1 ? "st" : row.rank === 2 ? "nd" : row.rank === 3 ? "rd" : "th"} · ${row.points} pts</strong><span>Leader: ${escapeHtml(leader.participant)} · ${row.points - leader.points} gap · P(1st): ${probability}</span></article>`;
}

function quality(fixtures) {
  const levels = fixtures.map((fixture) => fixture.availability?.data_quality || "LIMITED");
  const hasMarketForAll = fixtures.every((fixture) => fixture.market?.available);
  const confirmedLineups = fixtures.reduce((total, fixture) => total + (fixture.availability?.confirmed_lineups || 0), 0);
  if (levels.includes("STALE")) return ["STALE", "One or more required sources have not refreshed within tolerance."];
  if (levels.includes("DEGRADED")) {
    if (hasMarketForAll && confirmedLineups === 0) {
      return ["DEGRADED", "Market views are available. Confirmed lineups are not yet available — normal this far ahead of kickoff."];
    }
    if (!hasMarketForAll) return ["DEGRADED", "At least one fixture has no current market view. The forecast remains available."];
    return ["DEGRADED", "Some confirmed lineups are still incomplete. The forecast remains available."];
  }
  if (levels.includes("LIMITED")) return ["LIMITED", "Forecasts use the available historical and market evidence."];
  return ["GOOD", "All available sources are current for this forecast."];
}

function submissionMessage(round) {
  const messages = {
    NOT_READY: "Review when ready",
    READY: "Ready to enter manually",
    MARKED_AS_SUBMITTED: "Recorded as entered by you",
    CHANGED_SINCE_REVIEW: "Recommendations changed since your review",
    CHANGED_AFTER_SUBMISSION: "Review before the deadline",
    ROUND_CLOSED: "The deadline has passed",
  };
  return [titleCase(round.submission_state), messages[round.submission_state] || "Review your picks"];
}

function pageHeader(eyebrow, title, copy, actions = "") {
  return `<header class="page-header"><div><p class="eyebrow">${escapeHtml(eyebrow)}</p><h1>${escapeHtml(title)}</h1><p class="subtitle">${escapeHtml(copy)}</p></div>${actions}</header>`;
}

function fixtureCard(fixture, index, compact = false) {
  const pick = effectivePick(fixture);
  const overridden = fixture.user_pick && fixture.user_pick !== fixture.pick;
  const change = fixtureChange(index);
  const changedCopy = change ? `<div class="change-note">Changed · ${escapeHtml(change.fields.map((field) => `${field.was} → ${field.now}`).join(" · "))}</div>` : "";
  return `<button class="fixture-card ${overridden ? "overridden" : ""} ${change ? "changed" : ""} ${compact ? "compact" : ""}" data-fixture="${index}" aria-label="View detail for ${escapeHtml(fixture.home_team)} versus ${escapeHtml(fixture.away_team)}">
    <div class="card-topline"><div class="teams"><span class="team-line">${teamBadge(fixture.home_team)}${escapeHtml(fixture.home_team)}</span><span class="team-line">${teamBadge(fixture.away_team)}${escapeHtml(fixture.away_team)}</span></div>${overridden ? '<span class="status-pill neutral">YOUR PICK</span>' : '<span class="versus">v</span>'}</div>
    <div class="score" aria-label="Recommended score ${pick}"><strong>${pick.split("-")[0]}</strong><span>–</span><strong>${pick.split("-")[1]}</strong></div>
    <div class="metric-row"><div class="metric"><span>Result</span><strong>${percent(fixture.result_probability)}</strong></div><div class="metric"><span>Score</span><strong>${percent(fixture.score_probability)}</strong></div><div class="metric"><span>EV</span><strong>${fixture.expected_points.toFixed(2)}</strong></div></div>
    <div class="card-footer"><span>${overridden ? `Model: ${fixture.pick}` : `Model pick: ${fixture.model_pick}`}</span><span class="confidence ${fixture.confidence.toLowerCase()}">${fixture.confidence}</span></div>${changedCopy}
    <div class="card-footer"><span class="card-link">Why this pick?</span><span aria-hidden="true">›</span></div>
  </button>`;
}

function updateCountdown() {
  if (!appData) return;
  const deadline = new Date(appData.round.deadline_at).getTime();
  const delta = deadline - Date.now();
  const node = $("#countdown");
  if (!node) return;
  if (delta <= 0) { node.textContent = "Round closed"; return; }
  const total = Math.floor(delta / 1000);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  node.textContent = days ? `${days}d ${hours}h ${minutes}m remaining` : `${hours}h ${minutes}m remaining`;
}

function homeView() {
  const { round, settings } = appData;
  const [submissionState, submissionDetail] = submissionMessage(round);
  const [dataQuality, qualityCopy] = quality(round.fixtures);
  const changed = round.submission_state === "CHANGED_AFTER_SUBMISSION" || round.submission_state === "CHANGED_SINCE_REVIEW";
  const alerts = appData.alerts?.items || [];
  return `${pageHeader(`Super 6 — Round ${round.round_number}`, "Your round, in one view.", `Deadline ${displayDate(round.deadline_at)}`, `<div class="monitor-card"><span class="monitor-dot"></span><span>Monitoring active</span><small>Forecast refreshed ${displayDate(round.meta.generated_at)}</small></div>`)}
    <section class="command-strip" aria-label="Round status"><div class="deadline-card"><span class="eyebrow">Time to deadline</span><strong id="countdown">—</strong><span>${displayDate(round.deadline_at)}</span></div><div class="round-kpi"><span>Expected round score <button class="info" data-info="ev" aria-label="Explain expected points">?</button></span><strong>${round.expected_round_score.toFixed(2)}</strong><small>Across all six picks</small></div><div class="round-kpi submission-kpi"><span>Entry status</span><strong>${submissionState}</strong><small>${submissionDetail}</small></div></section>
    ${leagueSummary()}
    ${alerts.length ? `<button class="alert-strip" data-page="activity"><strong>${alerts.length} material update${alerts.length === 1 ? "" : "s"}</strong><span>${escapeHtml(alerts[0].summary)}</span><b>Review ›</b></button>` : ""}
    <section class="section-heading"><div><p class="eyebrow">Recommended picks</p><h2>Six scores to enter</h2></div><div class="button-row"><button class="button secondary-button" data-action="edit-picks">Edit picks</button><button class="button secondary-button" data-action="copy-picks">Copy picks</button></div></section>
    ${changed ? `<div class="change-alert"><strong>Action needed</strong> ${escapeHtml(submissionDetail)}. This app does not update Super 6 for you.</div>` : ""}
    <div class="fixture-grid">${round.fixtures.map((fixture, index) => fixtureCard(fixture, index)).join("")}</div>
    <section class="lower-grid"><article class="golden-card"><p class="eyebrow">Golden Goal</p><div class="golden-value">${round.golden_goal.minute}</div><p>Sky minute ${round.golden_goal.minute} = goal between ${round.golden_goal.sky_interval}.</p><button class="text-button" data-action="golden-detail">Why this minute?</button></article><article class="action-card"><p class="eyebrow">Next action</p><h2>${round.submission_state === "MARKED_AS_SUBMITTED" ? "Your picks are recorded" : "Ready to enter?"}</h2><p>${round.submission_state === "MARKED_AS_SUBMITTED" ? "You told us these scores were entered in Super 6. Any later recommendation change will be clearly flagged." : "Enter these predictions in Super 6 yourself, then record that action here."}</p><button class="button" data-action="mark-entered">${round.submission_state === "MARKED_AS_SUBMITTED" ? "Update entered picks" : "Mark as entered in Super 6"}</button></article><article class="quality-card"><p class="eyebrow">Data quality</p><h2>${dataQuality}</h2><p>${qualityCopy}</p><button class="text-button" data-page="health">View source status</button></article></section>`;
}

function intelligenceView() {
  const intelligence = appData.round.fixtures.flatMap((fixture) => (fixture.availability?.items || []).map((item) => ({ fixture, item })));
  const events = appData.activity.filter((event) => /MARKET|LINEUP|AVAILABILITY|CLAIM/.test(event.event_type));
  if (!intelligence.length && !events.length) return emptyState("No material intelligence yet", "When an approved source produces a relevant event, it will appear here with its provenance and whether it changed the forecast.");
  return `<div class="feed-list">${intelligence.map(({ fixture, item }) => `<article class="feed-item"><span class="severity important">${escapeHtml(item.certainty)}</span><div><h3>${escapeHtml(fixture.home_team)} v ${escapeHtml(fixture.away_team)}</h3><p>${escapeHtml(item.player_name)} — ${escapeHtml(titleCase(item.status))}</p><small>Source: ${escapeHtml(item.source)} · reliability: ${escapeHtml(item.source_reliability)} · quantitative impact: ${escapeHtml(item.quantitative_impact)}</small></div></article>`).join("")}${events.map(activityItem).join("")}</div>`;
}

function roundView() {
  const { round, results } = appData;
  const tabs = ["predictions", "intelligence", "changes", "setup", "results"];
  let content;
  if (roundTab === "intelligence") content = intelligenceView();
  else if (roundTab === "changes") {
    const visitChanges = round.changes_since_last_visit || [];
    const submissionChanges = round.submission?.drift || [];
    content = visitChanges.length || submissionChanges.length ? `<div class="feed-list">${visitChanges.map((change) => `<article class="feed-item"><span class="severity important">Changed</span><div><h3>${escapeHtml(change.home_team)} v ${escapeHtml(change.away_team)}</h3><p>${escapeHtml(change.fields.map((field) => `${titleCase(field.field)}: ${field.was} → ${field.now}`).join(" · "))}</p><small>Recorded in forecast version history. Review the fixture for its full audit trail.</small></div></article>`).join("")}${submissionChanges.map((change) => `<article class="feed-item"><span class="severity critical">Entry drift</span><div><h3>${escapeHtml(round.fixtures[change.index].home_team)} v ${escapeHtml(round.fixtures[change.index].away_team)}</h3><p>${escapeHtml(change.was)} → ${escapeHtml(change.now)}</p><small>Update the prediction in Super 6 yourself if you choose the new recommendation.</small></div></article>`).join("")}</div>` : emptyState("No changes since your last review", "The current recommendations are stable against your recorded picks.");
  }
  else if (roundTab === "setup") content = roundSetupView();
  else if (roundTab === "results") content = resultsView(results);
  else content = `<div class="fixture-grid">${round.fixtures.map((fixture, index) => fixtureCard(fixture, index)).join("")}</div>`;
  return `${pageHeader(`Round ${round.round_number}`, "Round control", "See every pick, evidence item, change, setup detail, and result in one place.")}
  <div class="tabs" role="tablist">${tabs.map((tab) => `<button class="tab ${roundTab === tab ? "active" : ""}" data-round-tab="${tab}" role="tab" aria-selected="${roundTab === tab}">${titleCase(tab)}</button>`).join("")}</div><section class="tab-panel">${content}</section>`;
}

function roundSetupView() {
  const { round } = appData;
  return `<section class="form-card"><div class="section-heading"><div><p class="eyebrow">Manual configuration</p><h2>Set up a Super 6 round</h2><p class="subtitle">Sky stays outside the system boundary. Add the six fixtures and confirm the UK deadline here.</p></div></div><form id="round-form"><div class="form-grid"><label>Round number<input required name="round_number" type="number" min="1" value="${round.round_number}"></label><label>Deadline (UK time)<input required name="deadline_at" type="datetime-local" value="${localDateTime(round.deadline_at)}"></label></div><div class="fixture-form-list">${round.fixtures.map((fixture, index) => `<div class="fixture-form-row"><span>${index + 1}</span><label><span class="sr-only">Fixture ${index + 1} home team</span><input required name="home_${index}" value="${escapeHtml(fixture.home_team)}" placeholder="Home team"></label><b>v</b><label><span class="sr-only">Fixture ${index + 1} away team</span><input required name="away_${index}" value="${escapeHtml(fixture.away_team)}" placeholder="Away team"></label></div>`).join("")}</div><div class="form-actions"><button class="button" type="submit">Save round &amp; refresh forecast</button></div></form></section>`;
}

function resultsView(results) {
  const completed = results.fixtures.filter((fixture) => fixture.status === "RESULTED" || fixture.status === "VOID").length;
  return `<section class="results-summary"><div><p class="eyebrow">Round score</p><strong>${results.summary.points} pts</strong><small>${results.summary.exact} exact · ${results.summary.correct} correct result · ${results.summary.void} void</small></div><p>${completed ? `${completed} of 6 fixtures have a recorded outcome.` : "Enter the final outcomes when the round is complete."}</p></section><section class="form-card"><h2>Record results</h2><p class="subtitle">Use <b>VOID</b> for postponed or abandoned fixtures. Void fixtures are excluded from performance, not counted as zero.</p><form id="results-form" class="result-entry-list">${results.fixtures.map((entry, index) => `<label><span>${escapeHtml(entry.fixture.home_team)} v ${escapeHtml(entry.fixture.away_team)}</span><input required name="result_${index}" value="${entry.actual === null ? "VOID" : escapeHtml(entry.actual || "")}" placeholder="e.g. 2-0 or VOID"></label>`).join("")}<div class="form-actions"><button class="button" type="submit">Save round results</button></div></form></section>`;
}

function emptyState(title, copy, action = "") {
  return `<section class="empty-state"><p class="eyebrow">Nothing to review</p><h2>${escapeHtml(title)}</h2><p>${escapeHtml(copy)}</p>${action}</section>`;
}

function leagueView() {
  const league = appData.league;
  const name = appData.settings.league_name;
  if (!league.standings.length) return `${pageHeader(name, "League position", "Add opponent predictions and entered picks, then record final results to build the private-league table.")}${emptyState("No scored private-league rounds yet", "Opponent selections are kept locally and never collected from Sky.", '<button class="button" data-page="opponents">Import opponent predictions</button>')}`;
  const first = league.finish_first ? percent(league.finish_first.probability) : "Not estimated";
  return `${pageHeader(name, "League position", "Every point below is calculated from your recorded picks and final results.")}<section class="stat-table-card"><table><thead><tr><th>Rank</th><th>Player</th><th>Points</th><th>Exact</th><th>Rounds</th><th>P(Finish 1st)</th></tr></thead><tbody>${league.standings.map((row) => `<tr class="${row.participant === appData.settings.player_name ? "you-row" : ""}"><td>${row.rank}</td><td>${escapeHtml(row.participant)}</td><td>${row.points}</td><td>${row.exact_scores}</td><td>${row.rounds_scored}</td><td>${row.participant === appData.settings.player_name ? first : "—"}</td></tr>`).join("")}</tbody></table></section><section class="callout"><strong>Strategy: ${escapeHtml(league.strategy.mode)}</strong><span>${escapeHtml(league.strategy.reason)} ${escapeHtml(league.finish_first_reason)}</span></section>`;
}

function roundKey() {
  return `${new Date(appData.round.deadline_at).getFullYear()}:${appData.round.round_number}`;
}

function opponentsView() {
  const profiles = appData.league.profiles || [];
  return `${pageHeader("Opponent modelling", "Your private league", "Import selections supplied through permitted channels. We never collect them from Sky.")}
  <section class="split-layout"><div>${profiles.length ? `<div class="opponent-list">${profiles.map((profile) => `<article class="opponent-card"><h2>${escapeHtml(profile.participant)}</h2><p>${profile.picks} recorded picks · ${profile.rounds} round${profile.rounds === 1 ? "" : "s"}</p><small>Home ${percent(profile.home_result_rate)} · Draw ${percent(profile.draw_result_rate)} · Away ${percent(profile.away_result_rate)} · Avg goals ${profile.average_total_goals}</small><small>Common scores: ${profile.common_scores.map(([score, count]) => `${score} (${count})`).join(" · ") || "Not enough history"}</small></article>`).join("")}</div>` : emptyState("No opponent history yet", "Add selections after each deadline. The system will learn only from recorded evidence.")}</div><section class="form-card"><p class="eyebrow">Manual import</p><h2>Import six selections</h2><p class="subtitle">Paste six scores in fixture order, one per line — for example 2-0. You will see the saved picks immediately; no Sky integration is used.</p><form id="opponent-form"><label>Name<input required name="name" maxlength="80" placeholder="e.g. Dave"></label><label>Predictions<textarea required name="picks" rows="7" placeholder="2-0&#10;1-1&#10;2-1&#10;1-2&#10;3-0&#10;1-2"></textarea></label><div class="form-actions"><button class="button" type="submit">Import selections</button></div></form></section></section>`;
}

function performanceView() {
  const { performance } = appData;
  const body = performance.arms.length ? `<section class="stat-table-card"><table><thead><tr><th>Arm</th><th>Rounds</th><th>Fixtures</th><th>Points</th><th>Pts / round</th><th>Exact</th></tr></thead><tbody>${performance.arms.map((arm) => `<tr><td>${escapeHtml(arm.arm)}</td><td>${arm.rounds}</td><td>${arm.fixtures}</td><td>${arm.points}</td><td>${Number(arm["pts/round"]).toFixed(2)}</td><td>${arm.exact}</td></tr>`).join("")}</tbody></table></section>` : emptyState("No performance data yet", "Record predictions before a deadline and score a completed round to begin the prospective comparison.");
  return `${pageHeader("Prospective measurement", "Performance", "The model is evaluated round by round, not retrospectively reconstructed.")}${body}<section class="callout muted"><strong>Read this carefully</strong><span>${escapeHtml(performance.sample_warning)}</span></section>`;
}

function activityItem(event) {
  return `<article class="feed-item"><span class="severity ${String(event.severity || "INFORMATIONAL").toLowerCase()}">${escapeHtml(titleCase(event.severity || "Informational"))}</span><div><h3>${escapeHtml(event.home_team && event.away_team ? `${event.home_team} v ${event.away_team}` : titleCase(event.event_type))}</h3><p>${escapeHtml(event.summary || titleCase(event.event_type))}</p><small>${displayDate(event.occurred_at)} · ${escapeHtml(titleCase(event.event_type))}</small></div></article>`;
}

function activityView() {
  const filters = { ALL: () => true, PREDICTIONS: (event) => /PREDICTION|CONFIDENCE/.test(event.event_type), INTELLIGENCE: (event) => /CLAIM|AVAILABILITY/.test(event.event_type), MARKETS: (event) => /MARKET/.test(event.event_type), LINEUPS: (event) => /LINEUP/.test(event.event_type), SYSTEM: (event) => /POLL|FORECAST/.test(event.event_type) };
  const visible = appData.activity.filter(filters[activityFilter] || filters.ALL);
  return `${pageHeader("Decision history", "Activity", "A chronological audit of material forecast, intelligence, strategy, and manual events.")}<div class="tabs activity-tabs">${Object.keys(filters).map((filter) => `<button class="tab ${activityFilter === filter ? "active" : ""}" data-activity-filter="${filter}">${titleCase(filter)}</button>`).join("")}</div>${visible.length ? `<div class="feed-list">${visible.map(activityItem).join("")}</div>` : emptyState("No matching activity", "Routine no-change refreshes are intentionally quiet.")}`;
}

function modelView() {
  const config = appData.model.frozen_config;
  const transparency = appData.model.transparency;
  const values = [["Model version", appData.model.version], ["Training config", "Frozen Phase 1"], ["Time decay ξ", config.xi], ["Market blend", config.market_blend_weight], ["Tail tolerance", config.tail_tolerance], ["Goal grid", `${config.min_goals_grid}–${config.max_goals_cap}`], ["Promoted-team priors", config.use_promoted_priors ? "Enabled" : "Disabled"]];
  return `${pageHeader("Expert", "Model Lab", "Technical detail is available here without competing with the weekly decision.")}<section class="callout muted"><strong>Frozen evaluation model</strong><span>${escapeHtml(appData.model.note)} ${escapeHtml(transparency.evaluation_reason || "")}</span></section><section class="spec-grid">${values.map(([label, value]) => `<article><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></article>`).join("")}</section><section class="form-card"><h2>How a pick is selected</h2><p>The scoreline with the highest expected Super 6 value is recommended. Expected points uses the probability of the selected match result plus the probability of the exact score; confidence remains a separate category.</p><p class="subtitle">Score matrices, calculation breakdowns and source freshness are available in each fixture’s detail view.</p></section>`;
}

function sourcesView() {
  return `${pageHeader("Evidence governance", "Sources", "Every source remains visible with its approval state, terms review and intended purpose.")}<div class="source-grid">${appData.sources.map((source) => `<article class="source-card"><div><span class="status-pill ${source.usage_status === "APPROVED" ? "approved" : source.usage_status === "PROHIBITED" ? "prohibited" : "neutral"}">${escapeHtml(titleCase(source.usage_status))}</span><h2>${escapeHtml(source.source_name)}</h2><p>${escapeHtml(source.acquisition_method)} · checked ${escapeHtml(source.terms_checked_at || "not yet")}</p></div><p>${escapeHtml(source.notes)}</p><small>Purpose: ${escapeHtml((source.purpose || []).join(", ") || "Not available")}</small>${source.terms_url ? `<a href="${escapeHtml(source.terms_url)}" target="_blank" rel="noreferrer">View terms</a>` : ""}</article>`).join("")}</div>`;
}

function healthView() {
  const health = appData.health;
  const ollama = health.ollama || {};
  const ollamaLabel = ollama.configured ? health.ollama_reachable : "Not configured";
  return `${pageHeader("Operational status", "System Health", "Health is visible when needed but stays out of the weekly decision flow.")}<section class="health-grid"><article><span>Scheduler</span><strong>${health.scheduler_jobs_pending ? `${health.scheduler_jobs_pending} pending` : "Ready"}</strong></article><article><span>Database</span><strong>Healthy · schema ${health.database_schema_version}</strong></article><article><span>Knowledge extractor</span><strong>${escapeHtml(health.extractor)} · ${escapeHtml(health.extractor_mode)}</strong></article><article><span>Ollama</span><strong>${escapeHtml(ollamaLabel)}</strong><small>${escapeHtml(ollama.detail || "")}</small></article></section>${health.warnings.length ? `<section class="warning-list">${health.warnings.map((warning) => `<p>${escapeHtml(warning)}</p>`).join("")}</section>` : ""}<section class="stat-table-card"><table><thead><tr><th>Source</th><th>Status</th><th>Quota</th></tr></thead><tbody>${health.sources.map((source) => `<tr><td>${escapeHtml(source.source_id)}</td><td>${escapeHtml(titleCase(source.usage_status))}</td><td>${source.quota_limit ? `${source.quota_used}/${source.quota_limit} per ${escapeHtml(source.quota_period)}` : "Not metered"}</td></tr>`).join("")}</tbody></table></section>`;
}

function settingsView() {
  const { settings } = appData;
  const toggles = Object.entries(settings.notifications).map(([key, enabled]) => `<label class="toggle-row"><span>${escapeHtml(titleCase(key))}</span><input type="checkbox" name="notification_${key}" ${enabled ? "checked" : ""}><i></i></label>`).join("");
  const delivery = settings.notification_delivery || { in_app: true, email: false };
  return `${pageHeader("Your preferences", "Settings", "Settings control presentation and alerts. They do not alter the forecasting model.")}<form id="settings-form" class="settings-grid"><section class="form-card"><p class="eyebrow">League</p><label>League name<input required name="league_name" maxlength="80" value="${escapeHtml(settings.league_name)}"></label><label>Your name<input required name="player_name" maxlength="80" value="${escapeHtml(settings.player_name)}"></label></section><section class="form-card"><p class="eyebrow">Notifications</p>${toggles}<label class="toggle-row"><span>In-app delivery</span><input type="checkbox" name="delivery_in_app" ${delivery.in_app ? "checked" : ""}><i></i></label><label class="toggle-row"><span>Email delivery</span><input type="checkbox" name="delivery_email" ${delivery.email ? "checked" : ""}><i></i></label><label>Email address<input name="notification_email" type="email" value="${escapeHtml(settings.notification_email || "")}" placeholder="you@example.com"></label><p class="subtitle">Email sends material prediction changes through your configured SMTP relay.</p></section><section class="form-card"><p class="eyebrow">Budget</p><label>Target monthly spend<input required name="target_monthly" type="number" min="0" step="1" value="${settings.budget.target_monthly}"></label><label>Hard monthly ceiling<input required name="hard_ceiling" type="number" min="0" step="1" value="${settings.budget.hard_ceiling}"></label><p class="subtitle">Current source spend is not yet connected; limits are saved here for the source-promotion flow.</p></section><div class="form-actions"><button class="button" type="submit">Save settings</button></div></form>`;
}

function moreView() {
  const pages = [["Activity", "activity", "Material forecast and intelligence history"], ["Model Lab", "model", "Frozen model and technical detail"], ["Sources", "sources", "Source approval and provenance"], ["System Health", "health", "Operational status and data warnings"], ["Settings", "settings", "League, notification and budget preferences"]];
  return `${pageHeader("More", "Everything else", "Expert detail stays out of the quick, final prediction review.")}<div class="more-list">${pages.map(([title, page, copy]) => `<button class="more-link" data-page="${page}"><span><strong>${title}</strong><small>${copy}</small></span><b>›</b></button>`).join("")}</div>`;
}

function renderPage() {
  if (!appData) return;
  const page = (location.hash || "#home").slice(1).split("?")[0] || "home";
  const views = { home: homeView, round: roundView, league: leagueView, opponents: opponentsView, performance: performanceView, activity: activityView, model: modelView, sources: sourcesView, health: healthView, settings: settingsView, more: moreView };
  $("#app").innerHTML = (views[page] || homeView)();
  document.querySelectorAll("[data-page-link]").forEach((link) => link.classList.toggle("active", link.dataset.pageLink === page));
  updateCountdown();
}

function reasonFor(fixture) {
  const [homeGoals, awayGoals] = fixture.pick.split("-").map(Number);
  const result = homeGoals === awayGoals ? "draw" : homeGoals > awayGoals ? `${fixture.home_team} win` : `${fixture.away_team} win`;
  const market = fixture.market?.available ? `The available market view assigns ${percent(fixture.market.home_probability)} to a home win, ${percent(fixture.market.draw_probability)} to a draw, and ${percent(fixture.market.away_probability)} to an away win.` : "No current market view is available, so this recommendation relies on the historical model only.";
  return { result, market };
}

function openFixture(index) {
  const fixture = appData.round.fixtures[index];
  const reason = reasonFor(fixture);
  const alternatives = fixture.alternatives.map((alternative) => `<tr class="${alternative.is_recommendation ? "recommended" : ""}"><td>${alternative.score}${alternative.is_recommendation ? " · PICK" : ""}</td><td>${percent(alternative.score_probability)}</td><td>${alternative.expected_points.toFixed(2)}</td></tr>`).join("");
  const evidence = fixture.availability?.items?.length ? fixture.availability.items.map((item) => `<li>${escapeHtml(item.player_name)} — ${escapeHtml(titleCase(item.status))}; ${escapeHtml(item.certainty)} via ${escapeHtml(item.source)}.</li>`).join("") : `<li>No individual team-news item is recorded for this fixture.</li>`;
  const history = fixture.history?.length ? fixture.history.map((version) => `<li>${escapeHtml(displayDate(version.recorded_at))}: ${escapeHtml(version.pick)} — ${escapeHtml(version.reason || "Forecast recorded")}</li>`).join("") : `<li>No earlier forecast version is persisted yet.</li>`;
  const [home, away] = effectivePick(fixture).split("-");
  $("#fixture-detail").innerHTML = `<div class="detail-header"><p class="eyebrow">Fixture recommendation</p><div class="detail-teams"><span>${teamBadge(fixture.home_team, "large")}${escapeHtml(fixture.home_team)}</span><b>v</b><span>${teamBadge(fixture.away_team, "large")}${escapeHtml(fixture.away_team)}</span></div><div class="detail-score">${escapeHtml(effectivePick(fixture).replace("-", " – "))}</div><p class="subtitle">Model ${fixture.pick}${fixture.user_pick ? ` · your pick ${fixture.user_pick}` : ""}</p></div><div class="detail-grid"><div class="detail-metric"><span>Result probability</span><strong>${percent(fixture.result_probability)}</strong></div><div class="detail-metric"><span>Exact-score probability</span><strong>${percent(fixture.score_probability)}</strong></div><div class="detail-metric"><span>Expected Super 6 points</span><strong>${fixture.expected_points.toFixed(2)}</strong></div><div class="detail-metric"><span>Confidence</span><strong class="confidence ${fixture.confidence.toLowerCase()}">${fixture.confidence}</strong></div></div><section class="why-card"><p class="eyebrow">Why ${fixture.pick}?</p><h3>${escapeHtml(reason.result)} is the highest-value result.</h3><p>This score produces the highest expected Super 6 score from the current probability distribution. It is a recommendation, not a certainty.</p><ul><li>${escapeHtml(reason.market)}</li><li>Data quality: ${escapeHtml(fixture.availability?.data_quality || "LIMITED")}.</li><li>Confidence is separate from both probability figures.</li></ul></section><section class="alternatives"><p class="eyebrow">Meaningful alternatives</p><table><thead><tr><th>Score</th><th>Score probability</th><th>Super 6 EV</th></tr></thead><tbody>${alternatives}</tbody></table></section><details class="disclosure"><summary>Evidence &amp; prediction history</summary><h3>Current evidence</h3><ul>${evidence}</ul><h3>Prediction history</h3><ul>${history}</ul></details><form id="override-form" data-index="${index}" class="inline-score-form"><p class="eyebrow">Your pick</p><label>Home goals<input aria-label="${escapeHtml(fixture.home_team)} goals" name="home" min="0" max="20" type="number" value="${home}"></label><span>–</span><label>Away goals<input aria-label="${escapeHtml(fixture.away_team)} goals" name="away" min="0" max="20" type="number" value="${away}"></label><button class="button" type="submit">Use this score</button></form>`;
  $("#fixture-dialog").showModal();
}

function openInfo(title, content) {
  $("#info-detail").innerHTML = `<p class="eyebrow">How to read this</p><h2>${escapeHtml(title)}</h2><p class="subtitle">${escapeHtml(content)}</p>`;
  $("#info-dialog").showModal();
}

function openEntryDialog() {
  const round = appData.round;
  const rows = round.fixtures.map((fixture) => `<div><span>${shortTeam(fixture.home_team)} ${escapeHtml(fixture.home_team)} v ${escapeHtml(fixture.away_team)} ${shortTeam(fixture.away_team)}</span><strong>${effectivePick(fixture)}</strong></div>`).join("");
  $("#entry-detail").innerHTML = `<p class="eyebrow">Manual entry confirmation</p><h2>Have you entered these in Super 6?</h2><p class="subtitle">This only records your confirmation. It does not submit or update anything on Sky.</p><div class="entry-list">${rows}<div><span>Golden Goal</span><strong>Minute ${round.golden_goal.minute}</strong></div></div><div class="dialog-actions"><button class="plain-button" data-close="entry-dialog">Not yet</button><button class="button" id="confirm-entered">Yes — mark as entered</button></div>`;
  $("#entry-dialog").showModal();
}

function openRoundConfirm(payload) {
  pendingRound = payload;
  const fixtureRows = payload.fixtures.map((fixture) => `<div><span>${escapeHtml(fixture.home)} v ${escapeHtml(fixture.away)}</span></div>`).join("");
  $("#round-detail").innerHTML = `<p class="eyebrow">Check deadline</p><h2>Confirm this Super 6 round</h2><p class="subtitle">The deadline controls monitoring frequency and final-prediction alerts. Confirm it is in UK time before activating the round.</p><div class="entry-list"><div><span>Round ${payload.round_number}</span><strong>${escapeHtml(payload.deadline_at.replace("T", " "))} UK</strong></div>${fixtureRows}</div><div class="dialog-actions"><button class="plain-button" data-close="round-dialog">Back</button><button class="button" id="confirm-round">Confirm &amp; start monitoring</button></div>`;
  $("#round-dialog").showModal();
}

async function post(path, body) {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Something went wrong.");
  return payload;
}

async function saveOverrides(picks) {
  await post("/api/picks", { picks, recommendations: appData.round.fixtures.map((fixture) => fixture.pick) });
  await loadApp();
}

async function markEntered() {
  await post("/api/submission/entered", { picks: appData.round.fixtures.map(effectivePick), golden_goal: appData.round.golden_goal.minute });
  $("#entry-dialog").close();
  toast("Marked as entered. Any later recommendation change will be clearly flagged.");
  await loadApp();
}

async function copyPicks() {
  const round = appData.round;
  const text = `${round.fixtures.map((fixture) => `${shortTeam(fixture.home_team)} ${effectivePick(fixture)} ${shortTeam(fixture.away_team)}`).join("\n")}\nGolden Goal: minute ${round.golden_goal.minute}`;
  try { await navigator.clipboard.writeText(text); toast("Picks copied as plain text."); } catch { toast("Copy is unavailable in this browser."); }
}

function toast(message) { const node = $("#toast"); node.textContent = message; node.hidden = false; setTimeout(() => { node.hidden = true; }, 4200); }

async function loadApp() {
  $("#error").hidden = true;
  try {
    const response = await fetch("/api/app", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not build the decision centre.");
    appData = payload;
    clearInterval(countdownTimer);
    countdownTimer = setInterval(updateCountdown, 30_000);
    $("#loading").hidden = true;
    $("#app").hidden = false;
    renderPage();
  } catch (error) {
    $("#loading").hidden = true;
    $("#error").textContent = `The decision centre could not load: ${error.message}`;
    $("#error").hidden = false;
  }
}

window.addEventListener("hashchange", renderPage);
document.addEventListener("click", async (event) => {
  const fixture = event.target.closest("[data-fixture]");
  if (fixture) openFixture(Number(fixture.dataset.fixture));
  const page = event.target.closest("[data-page]");
  if (page) location.hash = `#${page.dataset.page}`;
  const tab = event.target.closest("[data-round-tab]");
  if (tab) { roundTab = tab.dataset.roundTab; renderPage(); }
  const activityTab = event.target.closest("[data-activity-filter]");
  if (activityTab) { activityFilter = activityTab.dataset.activityFilter; renderPage(); }
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "mark-entered") openEntryDialog();
  if (action === "copy-picks") await copyPicks();
  if (action === "edit-picks") { location.hash = "#round"; roundTab = "predictions"; }
  if (action === "golden-detail") { const distribution = appData.round.golden_goal.distribution; openInfo("Golden Goal", `${appData.round.golden_goal.rationale}${distribution ? ` Most likely intervals: ${Object.entries(distribution.interval_probabilities).map(([label, value]) => `${label} ${percent(value)}`).join(", ")}. No goal: ${percent(distribution.no_goal_probability)}.` : ""}`); }
  if (event.target.closest("[data-info='ev']")) openInfo("Expected points", "This is the average Super 6 score this prediction would earn if the match were replayed many times under the model’s current probabilities. It is not a promise for one match.");
  const close = event.target.closest("[data-close]");
  if (close) $("#" + close.dataset.close).close();
  if (event.target.closest("#confirm-entered")) { try { await markEntered(); } catch (error) { toast(error.message); } }
  if (event.target.closest("#confirm-round")) {
    try {
      await post("/api/rounds", pendingRound); $("#round-dialog").close(); toast("Round saved and forecast refreshed."); await loadApp(); location.hash = "#home";
    } catch (error) { toast(error.message); }
  }
});

document.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  try {
    if (form.id === "override-form") {
      const picks = appData.round.fixtures.map(effectivePick);
      picks[Number(form.dataset.index)] = `${form.elements.home.value}-${form.elements.away.value}`;
      await saveOverrides(picks); $("#fixture-dialog").close(); toast("Your pick is saved separately from the model recommendation.");
    } else if (form.id === "round-form") {
      const fixtures = Array.from({ length: 6 }, (_, index) => ({ home: form.elements[`home_${index}`].value.trim(), away: form.elements[`away_${index}`].value.trim() }));
      openRoundConfirm({ round_number: Number(form.elements.round_number.value), deadline_at: `${form.elements.deadline_at.value}:00`, fixtures });
    } else if (form.id === "opponent-form") {
      const picks = form.elements.picks.value.trim().split(/[\s,]+/).filter(Boolean);
      await post("/api/opponents", { name: form.elements.name.value, picks }); toast("Opponent selections imported."); await loadApp();
    } else if (form.id === "results-form") {
      const results = Array.from({ length: 6 }, (_, index) => { const value = form.elements[`result_${index}`].value.trim().toUpperCase(); return value === "VOID" ? null : value; });
      await post("/api/results", { results }); toast("Round results saved."); await loadApp();
    } else if (form.id === "settings-form") {
      const notifications = Object.keys(appData.settings.notifications).reduce((out, key) => ({ ...out, [key]: form.elements[`notification_${key}`].checked }), {});
      await post("/api/settings", { league_name: form.elements.league_name.value, player_name: form.elements.player_name.value, notifications, notification_delivery: { in_app: form.elements.delivery_in_app.checked, email: form.elements.delivery_email.checked }, notification_email: form.elements.notification_email.value, budget: { target_monthly: form.elements.target_monthly.value, hard_ceiling: form.elements.hard_ceiling.value } });
      toast("Settings saved."); await loadApp();
    }
  } catch (error) { toast(error.message); }
});

loadApp();

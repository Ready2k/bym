"use strict";

let appData;
let countdownTimer;
let roundTab = "predictions";
let pendingRound;
let activityFilter = "ALL";
let cupForecast;
let ollamaModels = [];
let lmsEntrant = "";
let lmsPreview = "";
let lmsGraphWeek = "";
let appLoadController;
let lmsReplay = { data: null, season: null, usedClubs: [], suggestedUsedClubs: [], history: [], suggestedHistory: [], selectedTeam: "", revealed: false, eliminated: false, suggestedEliminated: false };

// The current English 92, taken from the same E0–E3 season data used by the
// model. Cup Match can select any of those clubs, so the crest list cannot be
// limited to the six clubs in the current Super 6 round. The public image URLs
// are intentionally separated from the names the data provider abbreviates.
// Unknown clubs still retain the accessible initials fallback in teamBadge.
const TEAM_BADGES = {
  "AFC Wimbledon": "https://assets.football-logos.cc/logos/england/256x256/afc-wimbledon.68931326.png",
  "Accrington": "https://assets.football-logos.cc/logos/england/256x256/accrington.985a895a.png",
  "Arsenal": "https://assets.football-logos.cc/logos/england/256x256/arsenal.e5528ede.png",
  "Aston Villa": "https://assets.football-logos.cc/logos/england/256x256/aston-villa.07a2646c.png",
  "Barnet": "https://assets.football-logos.cc/logos/england/256x256/barnet.0e3477dc.png",
  "Barnsley": "https://assets.football-logos.cc/logos/england/256x256/barnsley.eeaac65d.png",
  "Barrow": "https://assets.football-logos.cc/logos/england/256x256/barrow.6407ad23.png",
  "Birmingham": "https://assets.football-logos.cc/logos/england/256x256/birmingham.1026e178.png",
  "Blackburn": "https://assets.football-logos.cc/logos/england/256x256/blackburn-rovers.a6f1e611.png",
  "Blackpool": "https://assets.football-logos.cc/logos/england/256x256/blackpool.05310153.png",
  "Bolton": "https://assets.football-logos.cc/logos/england/256x256/bolton.4a68a472.png",
  "Bournemouth": "https://assets.football-logos.cc/logos/england/256x256/bournemouth.1a237aee.png",
  "Bradford": "https://assets.football-logos.cc/logos/england/256x256/bradford.019d7449.png",
  "Brentford": "https://assets.football-logos.cc/logos/england/256x256/brentford.e3296e20.png",
  "Brighton": "https://assets.football-logos.cc/logos/england/256x256/brighton.5da206a0.png",
  "Bristol City": "https://assets.football-logos.cc/logos/england/256x256/bristol-city.32f1f82d.png",
  "Bristol Rvs": "https://assets.football-logos.cc/logos/england/256x256/bristol-rovers.3396d3b9.png",
  "Bromley": "https://assets.football-logos.cc/logos/england/256x256/bromley.70df050b.png",
  "Burnley": "https://assets.football-logos.cc/logos/england/256x256/burnley.7baea7d7.png",
  "Burton": "https://assets.football-logos.cc/logos/england/256x256/burton.c4e07845.png",
  "Cambridge": "https://assets.football-logos.cc/logos/england/256x256/cambridge-united.ce4c866e.png",
  "Cardiff": "https://assets.football-logos.cc/logos/england/256x256/cardiff-city.f164becb.png",
  "Charlton": "https://assets.football-logos.cc/logos/england/256x256/charlton.68457b55.png",
  "Chelsea": "https://assets.football-logos.cc/logos/england/256x256/chelsea.ede8a2a7.png",
  "Cheltenham": "https://assets.football-logos.cc/logos/england/256x256/cheltenham.b8c84bf8.png",
  "Chesterfield": "https://assets.football-logos.cc/logos/england/256x256/chesterfield.c6832531.png",
  "Colchester": "https://assets.football-logos.cc/logos/england/256x256/colchester.fba7369a.png",
  "Coventry": "https://assets.football-logos.cc/logos/england/256x256/coventry-city.0d74fc5a.png",
  "Crawley Town": "https://assets.football-logos.cc/logos/england/256x256/crawley-town.4c72af78.png",
  "Crewe": "https://assets.football-logos.cc/logos/england/256x256/crewe.21e8c9f5.png",
  "Crystal Palace": "https://assets.football-logos.cc/logos/england/256x256/crystal-palace.53067b96.png",
  "Derby": "https://assets.football-logos.cc/logos/england/256x256/derby-county.ba26b540.png",
  "Doncaster": "https://assets.football-logos.cc/logos/england/256x256/doncaster-rovers.b36ecd25.png",
  "Everton": "https://assets.football-logos.cc/logos/england/256x256/everton.6b635cd7.png",
  "Exeter": "https://assets.football-logos.cc/logos/england/256x256/exeter.48c0a951.png",
  "Fleetwood Town": "https://assets.football-logos.cc/logos/england/256x256/fleetwood-town.84ce757f.png",
  "Fulham": "https://assets.football-logos.cc/logos/england/256x256/fulham.4c7ce48b.png",
  "Gillingham": "https://assets.football-logos.cc/logos/england/256x256/gillingham.d39bc314.png",
  "Grimsby": "https://assets.football-logos.cc/logos/england/256x256/grimsby.c8994bb0.png",
  "Harrogate": "https://assets.football-logos.cc/logos/england/256x256/harrogate-town.8d7f79d7.png",
  "Huddersfield": "https://assets.football-logos.cc/logos/england/256x256/huddersfield.944e6a57.png",
  "Hull": "https://assets.football-logos.cc/logos/england/256x256/hull-city.28918662.png",
  "Ipswich": "https://assets.football-logos.cc/logos/england/256x256/ipswich.bb8ea176.png",
  "Leeds": "https://assets.football-logos.cc/logos/england/256x256/leeds-united.26dba8f5.png",
  "Leicester": "https://assets.football-logos.cc/logos/england/256x256/leicester.9c13bc7c.png",
  "Leyton Orient": "https://assets.football-logos.cc/logos/england/256x256/leyton-orient.c7c4a14e.png",
  "Lincoln": "https://assets.football-logos.cc/logos/england/256x256/lincoln-city.d7003a2c.png",
  "Liverpool": "https://assets.football-logos.cc/logos/england/256x256/liverpool.99c48ae3.png",
  "Luton": "https://assets.football-logos.cc/logos/england/256x256/luton-town.b918012a.png",
  "Man City": "https://assets.football-logos.cc/logos/england/256x256/manchester-city.62f9d1f2.png",
  "Man United": "https://assets.football-logos.cc/logos/england/256x256/manchester-united.7ab9d343.png",
  "Mansfield": "https://assets.football-logos.cc/logos/england/256x256/mansfield.93b6fc2f.png",
  "Middlesbrough": "https://assets.football-logos.cc/logos/england/256x256/middlesbrough.b9f845aa.png",
  "Millwall": "https://assets.football-logos.cc/logos/england/256x256/millwall.07c9fbfe.png",
  "Milton Keynes Dons": "https://assets.football-logos.cc/logos/england/256x256/mk-dons.661a7209.png",
  "Newcastle": "https://assets.football-logos.cc/logos/england/256x256/newcastle.53b65b3d.png",
  "Newport County": "https://assets.football-logos.cc/logos/england/256x256/newport.5139ecec.png",
  "Northampton": "https://assets.football-logos.cc/logos/england/256x256/northampton.5a5f9731.png",
  "Norwich": "https://assets.football-logos.cc/logos/england/256x256/norwich-city.5cf24a54.png",
  "Nott'm Forest": "https://assets.football-logos.cc/logos/england/256x256/nottingham-forest.43b53a26.png",
  "Notts County": "https://assets.football-logos.cc/logos/england/256x256/notts-county.b135f072.png",
  "Oldham": "https://assets.football-logos.cc/logos/england/256x256/oldham.8e568e90.png",
  "Oxford": "https://assets.football-logos.cc/logos/england/256x256/oxford-united.881849cd.png",
  "Peterboro": "https://assets.football-logos.cc/logos/england/256x256/peterborough.c708671d.png",
  "Plymouth": "https://assets.football-logos.cc/logos/england/256x256/plymouth-argyle.be2526a7.png",
  "Port Vale": "https://assets.football-logos.cc/logos/england/256x256/port-vale.b1ba14a7.png",
  "Portsmouth": "https://assets.football-logos.cc/logos/england/256x256/portsmouth.1007a222.png",
  "Preston": "https://assets.football-logos.cc/logos/england/256x256/preston-north-end.c8495ff2.png",
  "QPR": "https://assets.football-logos.cc/logos/england/256x256/queens-park-rangers.520a466e.png",
  "Reading": "https://assets.football-logos.cc/logos/england/256x256/reading.97e75826.png",
  "Rotherham": "https://assets.football-logos.cc/logos/england/256x256/rotherham.8002b756.png",
  "Salford": "https://assets.football-logos.cc/logos/england/256x256/salford-city.b6285922.png",
  "Sheffield United": "https://assets.football-logos.cc/logos/england/256x256/sheffield-united.1c8f00ec.png",
  "Sheffield Weds": "https://assets.football-logos.cc/logos/england/256x256/sheffield-wednesday.41a8f439.png",
  "Shrewsbury": "https://assets.football-logos.cc/logos/england/256x256/shrewsbury.12fdf1c9.png",
  "Southampton": "https://assets.football-logos.cc/logos/england/256x256/southampton.2a25d8e7.png",
  "Stevenage": "https://assets.football-logos.cc/logos/england/256x256/stevenage.cb1c36fa.png",
  "Stockport": "https://assets.football-logos.cc/logos/england/256x256/stockport-county.82cbdd72.png",
  "Stoke": "https://assets.football-logos.cc/logos/england/256x256/stoke-city.d394721d.png",
  "Sunderland": "https://assets.football-logos.cc/logos/england/256x256/sunderland.2f55baea.png",
  "Swansea": "https://assets.football-logos.cc/logos/england/256x256/swansea-city.af33720d.png",
  "Swindon": "https://assets.football-logos.cc/logos/england/256x256/swindon-town.244d3daa.png",
  "Tottenham": "https://assets.football-logos.cc/logos/england/256x256/tottenham.f192bf50.png",
  "Tranmere": "https://assets.football-logos.cc/logos/england/256x256/tranmere.7f26ba66.png",
  "Walsall": "https://assets.football-logos.cc/logos/england/256x256/walsall.573843c4.png",
  "Watford": "https://assets.football-logos.cc/logos/england/256x256/watford.99d74158.png",
  "West Brom": "https://assets.football-logos.cc/logos/england/256x256/west-bromwich-albion.bc428244.png",
  "West Ham": "https://assets.football-logos.cc/logos/england/256x256/west-ham.c86eebf5.png",
  "Wigan": "https://assets.football-logos.cc/logos/england/256x256/wigan.9339b194.png",
  "Wolves": "https://assets.football-logos.cc/logos/england/256x256/wolves.2c773758.png",
  "Wrexham": "https://assets.football-logos.cc/logos/england/256x256/wrexham.8faea20f.png",
  "Wycombe": "https://assets.football-logos.cc/logos/england/256x256/wycombe.b55b5638.png",
};

const TEAM_BADGE_ALIASES = {
  "Accrington Stanley": "Accrington", "AFC Bournemouth": "Bournemouth",
  "Birmingham City": "Birmingham",
  "Blackburn Rovers": "Blackburn", "Bolton Wanderers": "Bolton",
  "Brighton and Hove Albion": "Brighton", "Brighton & Hove Albion": "Brighton",
  "Bristol Rovers": "Bristol Rvs", "Cambridge United": "Cambridge",
  "Cardiff City": "Cardiff", "Charlton Athletic": "Charlton", "Coventry City": "Coventry",
  "Derby County": "Derby", "Doncaster Rovers": "Doncaster", "Harrogate Town": "Harrogate",
  "Huddersfield Town": "Huddersfield", "Hull City": "Hull", "Ipswich Town": "Ipswich",
  "Leeds United": "Leeds", "Leeds Utd": "Leeds", "Leicester City": "Leicester",
  "Lincoln City": "Lincoln", "Luton Town": "Luton", "Man. City": "Man City",
  "Manchester City": "Man City", "Manchester United": "Man United", "Manchester Utd": "Man United",
  "Man Utd": "Man United", "Middlesbrough FC": "Middlesbrough", "Millwall FC": "Millwall",
  "MK Dons": "Milton Keynes Dons", "Newcastle United": "Newcastle", "Newcastle Utd": "Newcastle",
  "Newport": "Newport County", "Norwich City": "Norwich", "Nottingham Forest": "Nott'm Forest",
  "Notts Forest": "Nott'm Forest", "Oxford United": "Oxford", "Peterborough United": "Peterboro",
  "Plymouth Argyle": "Plymouth", "Portsmouth FC": "Portsmouth", "Preston North End": "Preston",
  "Queens Park Rangers": "QPR", "Rotherham United": "Rotherham", "Salford City": "Salford",
  "Sheffield Utd": "Sheffield United", "Sheffield Wednesday": "Sheffield Weds",
  "Stockport County": "Stockport", "Stoke City": "Stoke", "Swansea City": "Swansea",
  "Swindon Town": "Swindon", "Tottenham Hotspur": "Tottenham", "Spurs": "Tottenham",
  "West Bromwich Albion": "West Brom", "West Bromwich": "West Brom",
  "West Ham United": "West Ham", "West Ham Utd": "West Ham", "Wolverhampton": "Wolves",
  "Wolverhampton Wanderers": "Wolves", "Wrexham AFC": "Wrexham", "Wycombe Wanderers": "Wycombe",
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
  const badge = TEAM_BADGES[TEAM_BADGE_ALIASES[team] || team];
  const image = badge ? `<img src="${badge}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.hidden=true">` : "";
  // The initials are a visual fallback for a crest that failed to load, not
  // content: the team's full name is always rendered as real text beside the
  // badge. role="img" already makes descendants presentational, and
  // aria-hidden additionally keeps them out of the contrast calculation,
  // which cannot resolve a one-letter glyph sitting under an image.
  return `<span class="team-badge ${size}" role="img" aria-label="${escapeHtml(team)} badge"><b aria-hidden="true">${escapeHtml(shortTeam(team))}</b>${image}</span>`;
}

function effectivePick(fixture) {
  return fixture.user_pick || fixture.strategy_pick || fixture.pick;
}

// A team outside the training corpus gets the league-average prior, which is
// not "low confidence" — it can rank a non-league side above a real one. The
// UI withholds the pick rather than dressing that up as a recommendation.
function isUnmodellable(fixture) {
  return fixture.modellability?.modellable === false;
}

// A fixture outside the corpus but with a market has a usable forecast —
// the market's, not the model's. Only an unpriced one has nothing.
function isMarketOnly(fixture) {
  return fixture.forecast_basis === "MARKET_ONLY_FALLBACK";
}

function hasNoForecast(fixture) {
  return fixture.forecast_basis === "NONE";
}

function marketOnlyNote(fixture) {
  return `${unknownTeamList(fixture.modellability)} outside the ${fixture.modellability?.corpus || "training"} corpus, so this pick comes from the betting market rather than the model — the result probabilities are the market's, the scoreline shape a neutral prior. It is not PHASE1_MODEL_V1.`;
}

function unknownTeamList(modellability) {
  const teams = modellability?.unknown_teams || [];
  return teams.length === 1 ? `${teams[0]} is` : `${teams.join(" and ")} are`;
}

function modellabilityNote(modellability) {
  return `${unknownTeamList(modellability)} outside the ${modellability?.corpus || "training"} corpus, so the model has no team strength to work from and falls back to the league average. That can rank the unknown side above a genuinely weaker known team, which is why no pick is shown.`;
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

function confidenceMeaning(fixture) {
  if (fixture.confidence === "HIGH") return "Strongest call";
  if (fixture.confidence === "MEDIUM") return "Steadier pick";
  return "Close call";
}

function nextRoundAction(round) {
  if (round.submission_state === "MARKED_AS_SUBMITTED") {
    return {
      eyebrow: "Entry recorded",
      title: "Your manual entry is saved here",
      copy: "We will flag any material recommendation change before the deadline.",
      action: "Review all details",
      actionName: "edit-picks",
    };
  }
  if (round.submission_state === "CHANGED_AFTER_SUBMISSION" || round.submission_state === "CHANGED_SINCE_REVIEW") {
    return {
      eyebrow: "Action needed",
      title: "A recommendation changed after your review",
      copy: "Check the changed fixture, then update Super 6 yourself if you want to follow the new pick.",
      action: "Review changes",
      actionName: "edit-picks",
    };
  }
  return {
    eyebrow: "Next step",
    title: "Review, copy, then enter your six picks",
    copy: "Super 6 stays outside this app. Once you have entered them, record that here.",
    action: "Mark as entered",
    actionName: "mark-entered",
  };
}

function pageHeader(eyebrow, title, copy, actions = "") {
  return `<header class="page-header"><div><p class="eyebrow">${escapeHtml(eyebrow)}</p><h1>${escapeHtml(title)}</h1><p class="subtitle">${escapeHtml(copy)}</p></div>${actions}</header>`;
}

function fixtureCard(fixture, index, compact = false) {
  const pick = effectivePick(fixture);
  const overridden = fixture.user_pick && fixture.user_pick !== fixture.pick;
  const change = fixtureChange(index);
  if (hasNoForecast(fixture) && !fixture.user_pick) {
    return `<button class="fixture-card unmodellable ${compact ? "compact" : ""}" data-fixture="${index}" aria-label="No recommendation for ${escapeHtml(fixture.home_team)} versus ${escapeHtml(fixture.away_team)}">
      <div class="card-topline"><div class="teams"><span class="team-line">${teamBadge(fixture.home_team)}${escapeHtml(fixture.home_team)}</span><span class="team-line">${teamBadge(fixture.away_team)}${escapeHtml(fixture.away_team)}</span></div><span class="status-pill prohibited">NO PICK</span></div>
      <div class="score no-pick" aria-label="No recommendation available">—</div>
      <p class="unmodellable-note">${escapeHtml(modellabilityNote(fixture.modellability))} Add odds for this fixture and a market-based pick becomes available.</p>
      <div class="card-footer"><span class="card-link">See what the model returned</span><span aria-hidden="true">›</span></div>
    </button>`;
  }
  const changedCopy = change ? `<div class="change-note">Changed · ${escapeHtml(change.fields.map((field) => `${field.was} → ${field.now}`).join(" · "))}</div>` : "";
  return `<button class="fixture-card ${overridden ? "overridden" : ""} ${change ? "changed" : ""} ${compact ? "compact" : ""}" data-fixture="${index}" aria-label="View detail for ${escapeHtml(fixture.home_team)} versus ${escapeHtml(fixture.away_team)}">
    <div class="card-topline"><div class="teams"><span class="team-line">${teamBadge(fixture.home_team)}${escapeHtml(fixture.home_team)}</span><span class="team-line">${teamBadge(fixture.away_team)}${escapeHtml(fixture.away_team)}</span></div>${overridden ? '<span class="status-pill neutral">YOUR PICK</span>' : '<span class="versus">v</span>'}</div>
    <div class="score" aria-label="Recommended score ${pick}"><strong>${pick.split("-")[0]}</strong><span>–</span><strong>${pick.split("-")[1]}</strong></div>
    <div class="metric-row"><div class="metric"><span>Result</span><strong>${percent(fixture.result_probability)}</strong></div><div class="metric"><span>Score</span><strong>${percent(fixture.score_probability)}</strong></div><div class="metric"><span>EV</span><strong>${fixture.expected_points.toFixed(2)}</strong></div></div>
    <div class="card-footer"><span>${isMarketOnly(fixture) ? `Market pick: ${escapeHtml(fixture.pick)}` : overridden ? `Model: ${fixture.pick}` : `Model pick: ${fixture.model_pick}`}</span><span class="confidence ${fixture.confidence.toLowerCase()}">${confidenceMeaning(fixture)} · ${fixture.confidence}</span></div>${changedCopy}
    ${isMarketOnly(fixture) ? `<p class="basis-note">Market-derived — outside the model's corpus. Not PHASE1_MODEL_V1.</p>` : ""}
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

// "Monitoring active" was previously stated unconditionally, whether or
// not a scheduler job existed. Claim it only when one is actually pending.
function monitorCard() {
  const { round, health } = appData;
  const pending = health?.scheduler_jobs_pending || 0;
  const nextCheck = health?.next_scheduled_check;
  const refreshed = `Forecast refreshed ${displayDate(round.meta.generated_at)}`;
  if (!pending) {
    return `<div class="monitor-card idle"><span class="monitor-dot idle"></span><span>Monitoring not scheduled</span><small>${refreshed}. Refresh this page for the latest forecast, or run <code>bym watch</code> to enable automatic polling.</small></div>`;
  }
  const when = nextCheck ? ` · next check ${displayDate(nextCheck)}` : "";
  return `<div class="monitor-card"><span class="monitor-dot"></span><span>Monitoring active</span><small>${refreshed}${escapeHtml(when)}</small></div>`;
}

function homeView() {
  const { round, settings } = appData;
  const [submissionState, submissionDetail] = submissionMessage(round);
  const [dataQuality, qualityCopy] = quality(round.fixtures);
  const changed = round.submission_state === "CHANGED_AFTER_SUBMISSION" || round.submission_state === "CHANGED_SINCE_REVIEW";
  const alerts = appData.alerts?.items || [];
  const blank = round.fixtures.filter(hasNoForecast);
  const marketOnly = round.fixtures.filter(isMarketOnly);
  const name = (fixture) => `${fixture.home_team} v ${fixture.away_team}`;
  const corpus = (blank[0] || marketOnly[0])?.modellability?.corpus || "training";
  const unmodellableBanner = [
    blank.length ? `<div class="change-alert danger"><strong>${blank.length} fixture${blank.length === 1 ? "" : "s"} cannot be forecast</strong> ${escapeHtml(blank.map(name).join("; "))} — outside the ${escapeHtml(corpus)} corpus with no odds to fall back on. No pick is shown. The expected round score above still counts ${blank.length === 1 ? "this fixture" : "these fixtures"}, so it is overstated.</div>` : "",
    marketOnly.length ? `<div class="change-alert notice"><strong>${marketOnly.length} market-derived pick${marketOnly.length === 1 ? "" : "s"}</strong> ${escapeHtml(marketOnly.map(name).join("; "))} — outside the ${escapeHtml(corpus)} corpus, so the market supplied the result probabilities instead of the model. Usable, but not PHASE1_MODEL_V1.</div>` : "",
  ].join("");
  const action = nextRoundAction(round);
  const confidenceCounts = round.fixtures.reduce((counts, fixture) => ({ ...counts, [fixture.confidence]: (counts[fixture.confidence] || 0) + 1 }), {});
  return `${pageHeader(`Super 6 — Round ${round.round_number}`, "Your picks are ready.", `Deadline ${displayDate(round.deadline_at)}`, "")}
    <section class="round-hero" aria-label="Round status"><div class="hero-deadline"><span class="eyebrow">Time to deadline</span><strong id="countdown">—</strong><span>${displayDate(round.deadline_at)}</span></div><div class="hero-action"><p class="eyebrow">${escapeHtml(action.eyebrow)}</p><h2>${escapeHtml(action.title)}</h2><p>${escapeHtml(action.copy)}</p><div class="button-row"><button class="button" data-action="${action.actionName}">${escapeHtml(action.action)}</button>${round.submission_state !== "MARKED_AS_SUBMITTED" ? '<button class="button secondary-button" data-action="copy-picks">Copy picks</button>' : ''}</div></div><div class="hero-meta"><div><span>Expected round score <button class="info" data-info="ev" aria-label="Explain expected points">?</button></span><strong>${round.expected_round_score.toFixed(2)}</strong><small>Across all six picks</small></div><div><span>Forecast quality</span><strong>${dataQuality}</strong><small>${qualityCopy}</small></div></div></section>
    ${monitorCard()}
    ${alerts.length ? `<button class="alert-strip" data-page="activity"><strong>${alerts.length} material update${alerts.length === 1 ? "" : "s"}</strong><span>${escapeHtml(alerts[0].summary)}</span><b>Review ›</b></button>` : ""}
    <section class="section-heading picks-heading"><div><p class="eyebrow">Recommended picks</p><h2>Six scores to enter</h2></div><div class="pick-summary"><span>${confidenceCounts.HIGH || 0} strong</span><span>${confidenceCounts.MEDIUM || 0} steadier</span><span>${confidenceCounts.LOW || 0} close calls</span></div></section>
    ${changed ? `<div class="change-alert"><strong>Action needed</strong> ${escapeHtml(submissionDetail)}. This app does not update Super 6 for you.</div>` : ""}
    ${unmodellableBanner}
    <div class="fixture-grid">${round.fixtures.map((fixture, index) => fixtureCard(fixture, index)).join("")}</div>
    <section class="lower-grid"><article class="golden-card"><p class="eyebrow">Golden Goal</p><div class="golden-value">${round.golden_goal.minute}</div><p>Sky minute ${round.golden_goal.minute} = goal between ${round.golden_goal.sky_interval}.</p><button class="text-button" data-action="golden-detail">Why this minute?</button></article>${leagueSummary()}<article class="quality-card"><p class="eyebrow">Need a deeper review?</p><h2>Round detail</h2><p>Compare alternatives, investigate evidence or manage this round from one place.</p><button class="text-button" data-page="round">Open round control</button></article></section>`;
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
    content = submissionRecord(round) + (visitChanges.length || submissionChanges.length ? `<div class="feed-list">${visitChanges.map((change) => `<article class="feed-item"><span class="severity important">Changed</span><div><h3>${escapeHtml(change.home_team)} v ${escapeHtml(change.away_team)}</h3><p>${escapeHtml(change.fields.map((field) => `${titleCase(field.field)}: ${field.was} → ${field.now}`).join(" · "))}</p><small>Recorded in forecast version history. Review the fixture for its full audit trail.</small></div></article>`).join("")}${submissionChanges.map((change) => `<article class="feed-item"><span class="severity critical">Entry drift</span><div><h3>${escapeHtml(round.fixtures[change.index].home_team)} v ${escapeHtml(round.fixtures[change.index].away_team)}</h3><p>${escapeHtml(change.was)} → ${escapeHtml(change.now)}</p><small>Update the prediction in Super 6 yourself if you choose the new recommendation.</small></div></article>`).join("")}</div>` : emptyState("No changes since your last review", "The current recommendations are stable against your recorded picks."));
  }
  else if (roundTab === "setup") content = roundSetupView();
  else if (roundTab === "results") content = resultsView(results);
  else content = `<div class="fixture-grid">${round.fixtures.map((fixture, index) => fixtureCard(fixture, index)).join("")}</div>`;
  return `${pageHeader(`Round ${round.round_number}`, "Round control", "See every pick, evidence item, change, setup detail, and result in one place.")}
  <div class="tabs" role="tablist">${tabs.map((tab) => `<button class="tab ${roundTab === tab ? "active" : ""}" data-round-tab="${tab}" role="tab" aria-selected="${roundTab === tab}">${titleCase(tab)}</button>`).join("")}</div><section class="tab-panel">${content}</section>`;
}

function cupResultCard(result) {
  const prediction = result.fixture;
  const unmodellable = isUnmodellable(prediction);
  // A result can remain in memory while the local server is being restarted.
  // Use the fixture's provenance where possible, and never let older response
  // shapes prevent the user from seeing a valid forecast.
  const provenanceData = prediction?.explanation?.provenance || {};
  const corpus = result.corpus || prediction?.modellability?.corpus || "not recorded";
  const seasons = Array.isArray(result.seasons) && result.seasons.length >= 2
    ? `${result.seasons[0]}–${result.seasons[1]}`
    : "not recorded";
  const modelVersion = result.model_version || provenanceData.model_version || "not recorded";
  const provenance = `<p class="subtitle">Corpus ${escapeHtml(corpus)} · seasons ${escapeHtml(seasons)} · model ${escapeHtml(modelVersion)}</p>`;
  if (unmodellable) {
    const retry = result.extended ? "" : `<div class="form-actions"><button class="button secondary-button" data-action="cup-extended">Retry with lower divisions</button></div>`;
    return `<section class="form-card cup-result"><p class="eyebrow">No recommendation</p><div class="detail-teams"><span>${teamBadge(prediction.home_team, "large")}${escapeHtml(prediction.home_team)}</span><b>v</b><span>${teamBadge(prediction.away_team, "large")}${escapeHtml(prediction.away_team)}</span></div><div class="detail-score no-pick">—</div><section class="danger-card"><h3>The model cannot forecast this tie.</h3><p>${escapeHtml(modellabilityNote(prediction.modellability))}</p></section>${provenance}${retry}<details class="disclosure"><summary>Show the raw model output anyway</summary><p class="subtitle">Kept visible so the failure can be checked. That does not make it usable.</p><div class="detail-grid"><div class="detail-metric"><span>Returned score</span><strong>${escapeHtml(prediction.pick)}</strong></div><div class="detail-metric"><span>Result probability</span><strong>${percent(prediction.result_probability)}</strong></div><div class="detail-metric"><span>Expected points</span><strong>${prediction.expected_points.toFixed(2)}</strong></div></div></details></section>`;
  }
  const alternatives = prediction.alternatives.map((alternative) => `<tr class="${alternative.is_recommendation ? "recommended" : ""}"><td>${escapeHtml(alternative.score)}</td><td>${percent(alternative.score_probability)}</td><td>${alternative.expected_points.toFixed(2)}</td></tr>`).join("");
  return `<section class="form-card cup-result"><p class="eyebrow">Model forecast</p><div class="detail-teams"><span>${teamBadge(prediction.home_team, "large")}${escapeHtml(prediction.home_team)}</span><b>v</b><span>${teamBadge(prediction.away_team, "large")}${escapeHtml(prediction.away_team)}</span></div><div class="detail-score">${escapeHtml(prediction.pick.replace("-", " – "))}</div><div class="detail-grid"><div class="detail-metric"><span>Result probability</span><strong>${percent(prediction.result_probability)}</strong></div><div class="detail-metric"><span>Exact-score probability</span><strong>${percent(prediction.score_probability)}</strong></div><div class="detail-metric"><span>Expected points</span><strong>${prediction.expected_points.toFixed(2)}</strong></div><div class="detail-metric"><span>Confidence</span><strong class="confidence ${prediction.confidence.toLowerCase()}">${prediction.confidence}</strong></div></div>${provenance}<table class="cup-alternatives"><thead><tr><th>Alternative</th><th>Score probability</th><th>EV</th></tr></thead><tbody>${alternatives}</tbody></table><p class="subtitle">${escapeHtml(result.note)}</p></section>`;
}

function cupView() {
  const teams = (appData.teams || []).map((team) => `<option value="${escapeHtml(team)}"></option>`).join("");
  const result = cupForecast;
  const prediction = result ? result.fixture : null;
  return `${pageHeader("One-off forecast", "Cup Match", "Choose any home and away side. This forecast is separate from your active Super 6 round.")}<section class="split-layout"><section class="form-card"><p class="eyebrow">Cup tie</p><h2>Predict a match</h2><p class="subtitle">Start typing to pick a team the model knows. A team outside the corpus returns no forecast rather than a misleading one.</p><form id="cup-form"><datalist id="known-teams">${teams}</datalist><label>Home team<input required name="home" maxlength="80" list="known-teams" placeholder="e.g. Stoke" value="${escapeHtml(prediction?.home_team || "")}"></label><label>Away team<input required name="away" maxlength="80" list="known-teams" placeholder="e.g. Oldham" value="${escapeHtml(prediction?.away_team || "")}"></label><label class="toggle-row"><span>Include League One, League Two &amp; National League</span><input type="checkbox" name="extended" ${result?.extended ? "checked" : ""}><i></i></label><p class="subtitle">The wider corpus changes the cross-division fallback, so it is no longer the frozen PHASE1_MODEL_V1. Use it for cup-tie sanity checks, never for Super 6 picks.</p><div class="form-actions"><button class="button" type="submit">Get cup prediction</button></div></form></section>${result ? cupResultCard(result) : ""}</section>`;
}

// Last Man Standing: a separate small game reusing the same fitted router
// the Super 6 dashboard above just built, read as win/draw/loss instead of
// an exact score. `appData.lms` is refreshed in place from each mutation's
// response rather than reloading the whole app, since it does not touch
// Super 6's round/engine state.
function lmsStatusPill(alive) {
  return `<span class="status-pill ${alive ? "approved" : "prohibited"}">${alive ? "ALIVE" : "ELIMINATED"}</span>`;
}

function lmsRecommendationRows(candidates, canPick, plannedTeam, currentTeam) {
  if (!candidates.length) return `<tr><td colspan="7">No eligible clubs left this gameweek — every fixture involves a club already used.</td></tr>`;
  // Every eligible club, not a top ten. The table exists so an entrant
  // can disagree with the model, and the clubs worth disagreeing about
  // are precisely the ones the model ranked low.
  return candidates.map((c) => `<tr class="${c.team === lmsPreview ? "lineage-previewing" : ""}" data-lms-preview data-team="${escapeHtml(c.team)}">
    <td>${escapeHtml(c.team)}${c.team === plannedTeam ? ` <span class="status-pill approved">PLAN</span>` : ""}${c.team === currentTeam ? ` <span class="status-pill">YOUR PICK</span>` : ""}</td>
    <td>${escapeHtml(c.team)} ${c.is_home ? "v" : "@"} ${escapeHtml(c.opponent)}</td>
    <td>${percent(c.p_win)}</td>
    <td>${percent(c.p_draw)}</td>
    <td>${percent(c.p_loss)}</td>
    <td><span class="confidence ${c.confidence.toLowerCase()}">${c.confidence}</span></td>
    <td>${canPick && c.team !== currentTeam ? `<button class="button secondary-button" data-lms-pick data-team="${escapeHtml(c.team)}" data-opponent="${escapeHtml(c.opponent)}" data-home="${c.is_home}">${currentTeam ? "Switch" : "Pick"}</button>` : ""}</td>
  </tr>`).join("");
}

// The plan, not the weekly ranking, is the recommendation: a club can be
// used once all season, so the strongest club this gameweek is regularly
// one a later gameweek needs more. The comparison against taking the
// weekly favourite is shown rather than described, so the advice can be
// judged instead of trusted.
function lmsPlanCard(plan) {
  if (!plan || !plan.picks?.length) return "";
  const now = plan.picks[0];
  const rows = plan.picks.map((p) => `<tr>
    <td>GW${p.gameweek}</td>
    <td>${escapeHtml(p.team)}</td>
    <td>${escapeHtml(p.team)} ${p.is_home ? "v" : "@"} ${escapeHtml(p.opponent)}</td>
    <td>${percent(p.p_win)}</td>
    <td><span class="confidence ${p.confidence.toLowerCase()}">${p.confidence}</span></td>
  </tr>`).join("");
  let comparison;
  if (plan.picks.length < 2) {
    comparison = `<p class="subtitle">Only one gameweek of fixtures is available, so this is just the weekly favourite — no lookahead was possible.</p>`;
  } else if (plan.greedy_team && plan.greedy_team !== now.team) {
    comparison = `<p class="subtitle">Taking this gameweek's favourite (${escapeHtml(plan.greedy_team)}) instead survives the same ${plan.picks.length} gameweeks ${percent(plan.greedy_survival_p)} of the time — ${(plan.survival_gain * 100).toFixed(1)}pp worse. ${escapeHtml(now.team)} is picked because ${escapeHtml(plan.greedy_team)} is worth more later.</p>`;
  } else {
    comparison = `<p class="subtitle">This gameweek's favourite is also the planned pick — nothing in the next ${plan.picks.length} gameweeks is worth saving it for.</p>`;
  }
  const unplannable = plan.unplannable?.length
    ? `<p class="subtitle">No legal pick left for ${plan.unplannable.map((n) => `GW${n}`).join(", ")} — every club playing has been used.</p>`
    : "";
  return `<section class="lms-plan-card">
    <p class="eyebrow">Model allocation</p>
    <h3>${escapeHtml(now.team)} ${now.is_home ? "v" : "@"} ${escapeHtml(now.opponent)} — ${percent(now.p_win)} to win</h3>
    <p class="subtitle">Surviving all ${plan.picks.length} planned gameweek${plan.picks.length === 1 ? "" : "s"}: ${percent(plan.survival_p)}. Later gameweeks are advisory and re-planned every week.</p>
    ${comparison}${unplannable}
    <details class="disclosure"><summary>Show planned-picks table</summary>
      <div class="stat-table-card"><table><thead><tr><th>Gameweek</th><th>Club</th><th>Fixture</th><th>P(Win)</th><th>Conf</th></tr></thead><tbody>${rows}</tbody></table></div>
    </details>
  </section>`;
}

// Pick lineage — the path taken against the path recommended.
//
// Survival is a product across gameweeks, so the cost of one deviation is
// not the week it happened in: it multiplies through every week after it.
// A per-week bar chart hides exactly that. Two cumulative curves show the
// gap opening and, more importantly, show it never closing.
const LINEAGE_GEOM = { w: 720, h: 240, top: 24, right: 96, bottom: 40, left: 52 };

function lineagePoints(steps, key, geom) {
  const usable = steps.filter((s) => s[key] !== null && s[key] !== undefined);
  if (!usable.length) return [];
  const span = Math.max(1, steps.length - 1);
  const plotW = geom.w - geom.left - geom.right;
  const plotH = geom.h - geom.top - geom.bottom;
  return usable.map((s) => {
    const i = steps.indexOf(s);
    return {
      step: s,
      x: geom.left + (span === 0 ? 0 : (i / span) * plotW),
      y: geom.top + plotH * (1 - s[key]),
    };
  });
}

function lineagePath(points) {
  return points.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
}

function lineageChart(steps, selectedWeek, currentLabel = "Your modeled path") {
  const g = LINEAGE_GEOM;
  const yours = lineagePoints(steps, "survival_yours", g);
  const plan = lineagePoints(steps, "survival_plan", g);
  if (!yours.length && !plan.length) return "";

  const plotH = g.h - g.top - g.bottom;
  // Solid hairline gridlines, one shade off the surface — never dashed.
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => {
    const y = g.top + plotH * (1 - t);
    return `<line class="lineage-grid" x1="${g.left}" y1="${y}" x2="${g.w - g.right}" y2="${y}"></line>
      <text class="lineage-tick" x="${g.left - 8}" y="${y + 4}" text-anchor="end">${Math.round(t * 100)}%</text>`;
  }).join("");

  const span = Math.max(1, steps.length - 1);
  const plotW = g.w - g.left - g.right;
  const xLabels = steps.map((s, i) => {
    const x = g.left + (i / span) * plotW;
    return `<text class="lineage-tick" x="${x}" y="${g.h - g.bottom + 20}" text-anchor="middle">GW${s.gameweek}</text>`;
  }).join("");

  // The week the paths part. Marked because "where did this go wrong" is
  // the first question the chart is asked, and hunting for it in the
  // geometry is the reader doing work the chart should have done.
  // Only worth marking when the paths run together first. A divergence at
  // the opening week needs no pointer — the two lines already start apart,
  // and a rule drawn on the axis reads as stray text.
  const split = steps.findIndex((s) => s.delta !== null && s.delta !== undefined && Math.abs(s.delta) > 1e-9);
  const splitMark = split > 0 ? (() => {
    const x = g.left + (split / span) * plotW;
    return `<line class="lineage-split" x1="${x}" y1="${g.top}" x2="${x}" y2="${g.h - g.bottom}"></line>
      <text class="lineage-split-label" x="${x + 6}" y="${g.top + 12}">diverges here</text>`;
  })() : "";

  // Two labels at the same height are unreadable, and the case is common:
  // an entrant following the plan exactly produces one line, not two.
  const coincide = yours.length && plan.length
    && Math.abs(yours[yours.length - 1].y - plan[plan.length - 1].y) < 11;
  const endLabel = (points, cls, text, suppress = false) => {
    if (!points.length) return "";
    const last = points[points.length - 1];
    const label = suppress ? "" :
      `<text class="lineage-end ${cls}" x="${last.x + 12}" y="${last.y + 4}">${escapeHtml(text)}</text>`;
    return `<circle class="lineage-dot ${cls}" cx="${last.x}" cy="${last.y}" r="4.5"></circle>${label}`;
  };

  const hotspots = steps.map((s, i) => {
    const x = g.left + (i / span) * plotW;
    const parts = [`GW${s.gameweek}`, s.status.toLowerCase()];
    if (s.team) parts.push(`you: ${s.team}${s.p_win != null ? ` ${percent(s.p_win)}` : ""}`);
    if (s.plan_team) parts.push(`plan: ${s.plan_team}${s.plan_p_win != null ? ` ${percent(s.plan_p_win)}` : ""}`);
    if (s.survival_yours != null) parts.push(`alive ${percent(s.survival_yours)}`);
    if (s.survival_plan != null) parts.push(`vs ${percent(s.survival_plan)}`);
    // Hit target spans the whole band, not the 9px dot. It is a real
    // control: selecting a week updates the model explanation below.
    const width = span === 0 ? plotW : Math.max(24, plotW / span);
    const left = span === 0 ? g.left : x - width / 2;
    return `<g class="lineage-hit ${String(s.gameweek) === String(selectedWeek) ? "selected" : ""}"><rect data-lms-graph-week="${s.gameweek}" x="${left}" y="${g.top}"
      width="${width}" height="${plotH}" fill="transparent" role="button" tabindex="0"
      aria-label="Show model detail for gameweek ${s.gameweek}"></rect>
      <title>${escapeHtml(parts.join(" · "))}</title></g>`;
  }).join("");

  return `<svg class="lineage-chart" viewBox="0 0 ${g.w} ${g.h}" role="img"
    aria-label="Interactive cumulative survival probability graph by gameweek: ${escapeHtml(currentLabel)} against the recommended plan.">
    ${ticks}${xLabels}${splitMark}
    <path class="lineage-line plan" d="${lineagePath(plan)}"></path>
    <path class="lineage-line yours" d="${lineagePath(yours)}"></path>
    ${endLabel(plan, "plan", "plan", coincide)}
    ${endLabel(yours, "yours", coincide ? "same path" : "your path")}
    ${hotspots}
  </svg>`;
}

function lineageRows(steps) {
  return steps.map((s) => {
    const delta = s.delta === null || s.delta === undefined
      ? "—"
      : `${s.delta >= 0 ? "+" : ""}${(s.delta * 100).toFixed(1)}pp`;
    const deltaClass = s.delta === null || s.delta === undefined || Math.abs(s.delta) < 1e-9
      ? "" : (s.delta < 0 ? "lineage-down" : "lineage-up");
    return `<tr>
      <td>GW${s.gameweek}</td>
      <td><span class="lineage-status ${s.status.toLowerCase()}">${s.status === "BOOKED" ? "Booked" : "Planned"}</span></td>
      <td>${escapeHtml(s.team || "—")}</td>
      <td>${s.p_win == null ? "—" : percent(s.p_win)}</td>
      <td>${escapeHtml(s.plan_team || "—")}</td>
      <td>${s.plan_p_win == null ? "—" : percent(s.plan_p_win)}</td>
      <td class="${deltaClass}">${delta}</td>
      <td>${s.survival_yours == null ? "—" : percent(s.survival_yours)}</td>
    </tr>`;
  }).join("");
}

function lmsGameweekLocked(gw) {
  if (!gw?.deadline_at) return false;
  const kickoff = Date.parse(gw.deadline_at);
  return Number.isFinite(kickoff) && Date.now() >= kickoff;
}

function lmsFixtureLabel(step) {
  if (!step?.team) return "No club selected";
  if (!step.opponent) return step.team;
  return `${step.team} ${step.is_home === false ? "@" : "v"} ${step.opponent}`;
}

function lmsPlanLineage(plan) {
  if (!plan?.picks?.length) return null;
  let survival = 1;
  return {
    steps: plan.picks.map((pick) => {
      survival *= pick.p_win;
      return {
        gameweek: pick.gameweek, status: "PLANNED", team: pick.team,
        opponent: pick.opponent, is_home: pick.is_home, p_win: pick.p_win,
        plan_team: pick.team, plan_p_win: pick.p_win, delta: 0,
        survival_yours: survival, survival_plan: survival,
      };
    }),
    survival_yours: survival,
    survival_plan: survival,
  };
}

function lmsGraphFocus(step) {
  if (!step) return "";
  const delta = step.delta == null ? "No comparable baseline" :
    `${step.delta >= 0 ? "+" : ""}${(step.delta * 100).toFixed(1)}pp against the unconstrained plan`;
  const survival = step.survival_yours == null ? "Not currently priceable" : percent(step.survival_yours);
  const plan = step.survival_plan == null ? "—" : percent(step.survival_plan);
  return `<section class="lineage-focus" aria-live="polite"><div><p class="eyebrow">FOCUSED WEEK · GW${step.gameweek}</p><h3>${escapeHtml(lmsFixtureLabel(step))}</h3><p>${step.status === "BOOKED" ? "Booked choice" : "Modeled future choice"} · ${step.p_win == null ? "Win probability unavailable" : `${percent(step.p_win)} to win`}</p></div><div><span>Survival to this point</span><strong>${survival}</strong><small>Plan ${plan} · ${delta}</small></div></section>`;
}

function lmsLineageCard(lineage, isPreview = false, previewTeam = "") {
  if (!lineage?.steps?.length) return "";
  const steps = lineage.steps;
  if (!steps.some((step) => String(step.gameweek) === String(lmsGraphWeek))) {
    lmsGraphWeek = String(steps[0].gameweek);
  }
  const focused = steps.find((step) => String(step.gameweek) === String(lmsGraphWeek)) || steps[0];
  const yours = lineage.survival_yours;
  const plan = lineage.survival_plan;
  const gap = yours != null && plan != null ? (yours - plan) * 100 : null;
  const booked = steps.filter((s) => s.status === "BOOKED");
  const hasBooked = booked.length > 0;

  let verdict;
  if (gap === null) {
    verdict = `<p class="subtitle">Some weeks in this path can no longer be priced, so the comparison stops where the fixtures run out.</p>`;
  } else if (Math.abs(gap) < 0.05) {
    verdict = hasBooked
      ? `<p class="subtitle">Your picks match the plan — the two paths are the same line.</p>`
      : `<p class="subtitle">This is the model's current best use of your remaining clubs across the full horizon. It will be re-planned as fixtures and availability change.</p>`;
  } else {
    const comparable = hasBooked ? booked : steps;
    const worst = comparable.reduce((a, b) => ((b.delta ?? 0) < (a.delta ?? 0) ? b : a), comparable[0]);
    const cause = worst && worst.delta != null && worst.plan_team && worst.plan_team !== worst.team
      ? ` The biggest trade-off is GW${worst.gameweek}, where ${escapeHtml(worst.team)} is used instead of ${escapeHtml(worst.plan_team)}.`
      : "";
    verdict = `<p class="subtitle">Surviving the whole horizon on this path is <strong>${percent(yours)}</strong>, against <strong>${percent(plan)}</strong> for the reference plan — <strong>${gap.toFixed(1)}pp</strong>.${cause} Because survival multiplies, a single week's deficit is carried by every week after it.</p>`;
  }

  const heading = isPreview
    ? `<p class="eyebrow">Preview — not saved</p>
       <h3>If you picked ${escapeHtml(previewTeam)}</h3>
       <p class="subtitle"><button class="text-button" data-lms-preview-clear>Back to your actual path</button></p>`
    : `<p class="eyebrow">Interactive horizon graph</p><h3>${hasBooked ? "Your path against the plan" : "Your modeled path across future gameweeks"}</h3>`;
  const graphLabel = hasBooked ? "Your path" : "Your modeled path";
  return `<section class="stat-table-card lineage-card${isPreview ? " lineage-preview" : ""}">
    ${heading}
    <div class="lineage-figure">
      <p class="lineage-hero">${yours == null ? "—" : percent(yours)}</p>
      <p class="subtitle">modeled chance of surviving all ${steps.length} gameweek${steps.length === 1 ? "" : "s"} on this path</p>
    </div>
    <div class="lineage-legend">
      <span><i class="lineage-swatch yours"></i>${graphLabel}</span>
      <span><i class="lineage-swatch plan"></i>Recommended plan</span>
    </div>
    <div class="lineage-week-selector" role="group" aria-label="Focus a gameweek">${steps.map((step) => `<button class="lineage-week ${String(step.gameweek) === String(focused.gameweek) ? "active" : ""}" data-lms-graph-week="${step.gameweek}" type="button">GW${step.gameweek}</button>`).join("")}</div>
    <div class="lineage-scroll">${lineageChart(steps, focused.gameweek, graphLabel)}</div>
    ${lmsGraphFocus(focused)}
    ${verdict}
    <details class="disclosure"><summary>Week by week</summary>
      <table><thead><tr><th>GW</th><th>Status</th><th>Your club</th><th>P(Win)</th><th>Plan's club</th><th>P(Win)</th><th>Delta</th><th>Still alive</th></tr></thead>
      <tbody>${lineageRows(steps)}</tbody></table>
    </details>
  </section>`;
}

function lmsHistoryRows(entrants) {
  const names = Object.keys(entrants).sort();
  if (!names.length) return `<tr><td colspan="4">No entrants recorded yet — enter a name above and record a pick.</td></tr>`;
  return names.map((name) => {
    const e = entrants[name];
    const last = e.history[e.history.length - 1];
    const provenance = last?.source ? ` (${last.source.toLowerCase()})` : "";
    return `<tr><td>${escapeHtml(name)}</td><td>${lmsStatusPill(e.alive)}</td><td>${e.used_clubs.length}</td><td>${last ? `GW${last.gameweek} ${escapeHtml(last.team)} — ${last.result}${provenance}` : "—"}</td></tr>`;
  }).join("");
}

function lmsAutoSettleBanner(autoSettle) {
  if (!autoSettle) return "";
  if (autoSettle.settled?.length) {
    const items = autoSettle.settled.map(([name, gw, result]) => `${escapeHtml(name)} GW${gw} ${result}`).join(", ");
    return `<div class="callout"><strong>Auto-settled</strong><span>Fetched from football-data.org just now: ${items}.</span></div>`;
  }
  if (autoSettle.ran) return "";
  return `<div class="callout muted"><strong>Manual mode</strong><span>${escapeHtml(autoSettle.note || "Automatic settlement is unavailable.")} Results entered below are logged as self-reported so other entrants can see they weren't independently verified.</span></div>`;
}

function lmsFixturesSourceBanner(fixturesSource) {
  if (!fixturesSource || fixturesSource.mode === "auto") return "";
  return `<div class="callout muted"><strong>Fixtures</strong><span>${escapeHtml(fixturesSource.note || "Using manually entered fixtures.")}</span></div>`;
}

function replaySuggestedPick(data) {
  const planned = data.suggested_path?.[0];
  if (planned) return planned;
  // A running local server may still be serving the earlier replay API
  // until it is restarted. Keep the comparison useful in that short window
  // by falling back to the former one-game model favourite.
  const candidate = data.candidates?.find((item) => item.team === data.model_pick);
  return candidate ? { team: candidate.team, p_win: candidate.p_win } : null;
}

function replaySuggestedWon(data, suggested) {
  if (data.suggested_won !== undefined && data.suggested_won !== null) {
    return data.suggested_won;
  }
  return data.candidates?.find((item) => item.team === suggested?.team)?.won;
}

function openLmsReplayPath() {
  const suggestedByGame = new Map(lmsReplay.suggestedHistory.map((entry) => [entry.game, entry]));
  const outcome = (won) => won ? "Survived" : "Out";
  const odds = (entry) => entry?.pWin == null ? "Probability unavailable" : `${percent(entry.pWin)} to win`;
  const choice = (label, entry, route) => `<article class="replay-tree-choice ${route} ${entry?.won ? "survived" : "out"}"><span>${label}</span><strong>${entry ? escapeHtml(entry.team) : "Route ended"}</strong><small>${entry ? `${odds(entry)} · ${outcome(entry.won)}` : "No further pick"}</small></article>`;
  const games = lmsReplay.history.map((entry) => {
    const suggested = suggestedByGame.get(entry.game);
    if (suggested && entry.team === suggested.team) {
      return `<section class="replay-tree-game merged"><p class="eyebrow">Game ${entry.game} · routes together</p>${choice("Both chose", { ...entry, pWin: suggested.pWin, won: suggested.won }, "shared")}</section>`;
    }
    return `<section class="replay-tree-game forked"><p class="eyebrow">Game ${entry.game} · route splits</p><div class="replay-tree-branches">${choice("Your pick", entry, "yours")}${choice("Suggested pick", suggested, "suggested")}</div></section>`;
  }).join("");
  $("#lms-replay-detail").innerHTML = `<p class="eyebrow">Replay results</p><h2>Your decision tree</h2><p class="subtitle">Each branch shows the club picked, its pre-game chance of winning, and the result. Where the routes select the same club they merge; where they differ, they split. The suggested route is re-planned before each game—never with hindsight.</p><div class="replay-tree"><div class="replay-tree-root"><span>Season start</span><strong>${escapeHtml(String(lmsReplay.season))}/${String(Number(lmsReplay.season) + 1).slice(2)}</strong></div>${games}</div>`;
  $("#lms-replay-dialog").showModal();
}

function openLiveLmsPath(entrant, state) {
  const resultLabel = (result) => result === "WIN" ? "Survived" : result === "PENDING" ? "Pending" : result ? "Out" : "Result unavailable";
  const resultClass = (result) => result === "WIN" ? "survived" : result === "PENDING" || !result ? "pending" : "out";
  const odds = (value) => value == null ? "Probability was not recorded" : `${percent(value)} to win`;
  const choice = (label, entry, route, result, pWin) => `<article class="replay-tree-choice ${route} ${resultClass(result)}"><span>${label}</span><strong>${entry ? escapeHtml(entry) : "No recorded suggestion"}</strong><small>${entry ? `${odds(pWin)} · ${resultLabel(result)}` : "This legacy pick predates route tracking"}</small></article>`;
  const games = [...state.history].sort((a, b) => a.gameweek - b.gameweek).map((entry) => {
    const same = entry.suggested_team && entry.team === entry.suggested_team;
    if (same) {
      return `<section class="replay-tree-game merged"><p class="eyebrow">Game ${entry.gameweek} · routes together</p>${choice("Both chose", entry.team, "shared", entry.result, entry.picked_p_win ?? entry.suggested_p_win)}</section>`;
    }
    const suggestedResult = entry.suggested_team === entry.team ? entry.result : null;
    return `<section class="replay-tree-game forked"><p class="eyebrow">Game ${entry.gameweek} · route splits</p><div class="replay-tree-branches">${choice("Your pick", entry.team, "yours", entry.result, entry.picked_p_win)}${choice("Suggested at pick time", entry.suggested_team, "suggested", suggestedResult, entry.suggested_p_win)}</div></section>`;
  }).join("");
  $("#lms-replay-detail").innerHTML = `<p class="eyebrow">Live Last Man Standing</p><h2>${escapeHtml(entrant)}’s decision tree</h2><p class="subtitle">Your route uses recorded picks and results. The other route is the plan saved when each pick was made, respecting clubs already used. Where the suggested club differs, its historical result is unavailable unless it was also your pick.</p><div class="replay-tree"><div class="replay-tree-root"><span>Competition start</span><strong>${escapeHtml(entrant)}</strong></div>${games}</div>`;
  $("#lms-replay-dialog").showModal();
}

function lmsReplayLab(lms) {
  const seasons = lms.replay_seasons || [];
  if (!seasons.length) return "";
  const selectedSeason = lmsReplay.season || seasons[seasons.length - 1];
  const options = seasons.map((season) => `<option value="${season}" ${season === selectedSeason ? "selected" : ""}>${season}/${String(season + 1).slice(2)}</option>`).join("");
  const data = lmsReplay.data;
  const controls = `<form id="lms-replay-form" class="form-card"><p class="eyebrow">Historical simulation</p><h3>Replay a season</h3><p class="subtitle">Make a legal pick, reveal the completed result, then try to survive the next game. This never changes the live LMS competition.</p><div class="form-grid"><label>Season<select name="season">${options}</select></label></div><div class="form-actions"><button class="button" type="submit">${data ? "Restart replay" : "Start replay"}</button>${data ? '<button class="button secondary-button" type="button" data-lms-replay-reset>Reset</button>' : ""}</div></form>`;
  if (!data) return controls;

  const game = data.game ?? data.slate;
  const totalGames = data.total_games ?? data.total_slates;
  const picked = data.candidates.find((candidate) => candidate.team === lmsReplay.selectedTeam);
  const suggested = lmsReplay.suggestedEliminated ? null : replaySuggestedPick(data);
  const suggestedWon = replaySuggestedWon(data, suggested);
  const market = data.candidates.find((candidate) => candidate.team === data.market_pick);
  const result = lmsReplay.revealed && picked
    ? (picked.won
      ? `<div class="callout"><strong>You survive</strong><span>${escapeHtml(picked.team)} won. That club is now spent for the rest of this replay.</span></div>`
      : `<div class="danger-card"><h3>You are out</h3><p>${escapeHtml(picked.team)} did not win, so this replay ends here. Restart to take another path.</p></div>`)
    : "";
  const comparison = lmsReplay.revealed
    ? `<p class="subtitle">Suggested pick: ${suggested ? `${escapeHtml(suggested.team)} — ${suggestedWon ? "won" : "did not win"}` : "No legal route"}${market ? ` · Price-proxy pick: ${escapeHtml(market.team)} — ${market.won ? "won" : "did not win"}` : ""}</p>`
    : `<p class="subtitle">Suggested plan: ${suggested ? `${escapeHtml(suggested.team)} (${percent(suggested.p_win)})` : "No legal route"}${suggested && data.suggested_path.length > 1 ? ` · then ${data.suggested_path.slice(1).map((pick) => escapeHtml(pick.team)).join(", ")}` : ""}${market ? ` · Price-proxy: ${escapeHtml(market.team)}` : ""}</p>`;
  const fixtureResults = lmsReplay.revealed
    ? `<details class="disclosure"><summary>Show game results</summary><div class="fixture-form-list">${data.fixtures.map((fixture) => `<div class="predicted-fixture"><span>${escapeHtml(fixture.home)} v ${escapeHtml(fixture.away)}</span><strong>${fixture.score}</strong></div>`).join("")}</div></details>`
    : "";
  const candidateRows = data.candidates.map((candidate) => {
    const selected = candidate.team === lmsReplay.selectedTeam;
    const outcome = lmsReplay.revealed ? `<td>${candidate.won ? "Won" : "No win"}</td>` : "";
    const action = lmsReplay.revealed || lmsReplay.eliminated
      ? (selected ? "Selected" : "")
      : `<button class="button ${selected ? "secondary-button" : "plain-button"}" type="button" data-lms-replay-pick="${escapeHtml(candidate.team)}">${selected ? "Selected" : "Choose"}</button>`;
    return `<tr class="${selected ? "you-row" : ""}"><td>${teamBadge(candidate.team)} ${escapeHtml(candidate.team)}</td><td>${candidate.is_home ? "v" : "@"} ${escapeHtml(candidate.opponent)}</td><td>${percent(candidate.p_win)}</td><td>${candidate.confidence}</td>${outcome}<td>${action}</td></tr>`;
  }).join("");
  const continueControl = lmsReplay.revealed && picked?.won && game < totalGames
    ? '<button class="button" type="button" data-lms-replay-next>Continue to next game</button>'
    : lmsReplay.revealed && picked?.won
      ? '<p class="subtitle">You survived every available game in this season.</p>'
      : "";
  const revealControl = picked && !lmsReplay.revealed
    ? '<button class="button" type="button" data-lms-replay-reveal>Reveal result</button>'
    : !picked && !lmsReplay.revealed ? '<p class="subtitle">Choose one eligible club to lock in your replay pick.</p>' : "";
  const history = lmsReplay.history.length
    ? `<details class="disclosure"><summary>Your replay history (${lmsReplay.history.length})</summary><p class="subtitle">${lmsReplay.history.map((entry) => `Game ${entry.game}: ${escapeHtml(entry.team)} — ${entry.won ? "survived" : "out"}`).join(" · ")}</p></details><button class="plain-button" type="button" data-lms-replay-path>View your path vs suggested</button>`
    : "";

  return `${controls}<section class="lms-plan-card"><p class="eyebrow">${escapeHtml(data.season + "/" + String(data.season + 1).slice(2))} · game ${game} of ${totalGames}</p><h3>Choose before you reveal</h3><p class="subtitle">${escapeHtml(data.slate_note)}</p>${comparison}${result}<div class="stat-table-card lms-candidate-card"><table><thead><tr><th>Club</th><th>Fixture</th><th>P(Win)</th><th>Confidence</th>${lmsReplay.revealed ? "<th>Result</th>" : ""}<th></th></tr></thead><tbody>${candidateRows}</tbody></table></div><div class="button-row" style="margin-top:1rem">${revealControl}${continueControl}</div>${fixtureResults}${history}</section>`;
}

function lmsView() {
  const lms = appData.lms || { gameweek: null, entrants: {}, recommendations: {}, plans: {}, unknown_teams: [], auto_settle: null, fixtures_source: null };
  const gw = lms.gameweek;
  const entrants = lms.entrants || {};
  if (!lmsEntrant) lmsEntrant = Object.keys(entrants)[0] || "";
  const state = entrants[lmsEntrant];
  const candidates = (lmsEntrant && lms.recommendations[lmsEntrant]) || lms.recommendations.__all__ || [];
  const plan = (lmsEntrant && lms.plans?.[lmsEntrant]) || lms.plans?.__all__ || null;
  const lineage = (lmsEntrant && lms.lineages?.[lmsEntrant]) || lmsPlanLineage(plan);
  const previewLineage = lmsPreview ? lms.previews?.[lmsEntrant]?.[lmsPreview] : null;
  const switchCandidates = (lmsEntrant && lms.switch_candidates?.[lmsEntrant]) || candidates;
  const decisionWeek = (lmsEntrant && lms.decision_weeks?.[lmsEntrant]) ?? gw?.number;
  const pendingPick = gw && state?.history.find((h) => h.gameweek === gw.number);
  const eliminated = state && !state.alive;

  const entrantSection = `<section class="form-card"><p class="eyebrow">Entrant</p><label>Name<input id="lms-entrant-input" list="lms-entrants" maxlength="60" value="${escapeHtml(lmsEntrant)}" placeholder="Your name"></label><datalist id="lms-entrants">${Object.keys(entrants).sort().map((n) => `<option value="${escapeHtml(n)}">`).join("")}</datalist>${state ? `<p>${lmsStatusPill(state.alive)} · ${state.used_clubs.length} club${state.used_clubs.length === 1 ? "" : "s"} used${state.used_clubs.length ? `: ${escapeHtml(state.used_clubs.join(", "))}` : ""}</p>${state.history.length ? '<button class="plain-button" type="button" data-lms-live-path>View live decision tree</button>' : ""}` : `<p class="subtitle">New entrant — no picks recorded yet.</p>`}</section>`;

  let pickSection;
  if (!gw) {
    pickSection = emptyState(
      "No gameweek fixtures yet",
      lms.fixtures_source?.note || "Fixtures are fetched automatically from football-data.org — this should fill in on the next refresh."
    );
  } else if (eliminated) {
    pickSection = `<div class="danger-card"><h3>${escapeHtml(lmsEntrant)} is eliminated</h3><p>No more picks can be recorded for this entrant.</p></div>`;
  } else if (pendingPick) {
    const settled = pendingPick.result !== "PENDING";
    const manualFallback = !settled && !lms.auto_settle?.ran
      ? `<span class="button-row" style="margin-top:.6rem">${["WIN", "DRAW", "LOSS"].map((r) => `<button class="button secondary-button" data-lms-result="${r}">${r}</button>`).join("")}</span><p class="subtitle" style="margin-top:.4rem">Manual override — logged as self-reported, not verified.</p>`
      : !settled ? `<p class="subtitle" style="margin-top:.4rem">Waiting for full time. This settles itself automatically once football-data.org has the result.</p>` : "";
    // A pending pick is provisional — the format allows a change right up
    // until the round kicks off — so the club table stays on screen with
    // the alternatives, rather than the callout replacing everything.
    const locked = settled || lmsGameweekLocked(gw);
    const lockNote = locked
      ? `<p class="subtitle">This gameweek has started — ${escapeHtml(pendingPick.team)} is final.</p>`
      : `<p class="subtitle">You can switch club until the first match kicks off${gw.deadline_at ? ` (${displayDate(gw.deadline_at)})` : ""}. Select any row below to see what it would do to your horizon.</p>`;
    pickSection = `<div class="callout muted"><strong>Gameweek ${gw.number}</strong><span>${escapeHtml(pendingPick.team)} ${pendingPick.is_home ? "v" : "@"} ${escapeHtml(pendingPick.opponent)} — ${pendingPick.result}${pendingPick.source ? ` (${pendingPick.source.toLowerCase()})` : ""}${manualFallback}</span></div>${lockNote}${lmsLineageCard(previewLineage || lineage, !!previewLineage, lmsPreview)}${lmsPlanCard(plan)}${locked ? "" : `<div class="stat-table-card lms-candidate-card"><div class="table-intro"><p class="eyebrow">Every eligible club — gameweek ${decisionWeek}</p><p class="subtitle">Select a row to see the horizon that club leads to.</p></div><table><thead><tr><th>Team</th><th>Fixture</th><th>P(Win)</th><th>P(Draw)</th><th>P(Loss)</th><th>Conf</th><th></th></tr></thead><tbody>${lmsRecommendationRows(switchCandidates, !!lmsEntrant, plan?.picks?.[0]?.team, pendingPick.team)}</tbody></table></div>`}`;
  } else {
    pickSection = `${lmsLineageCard(previewLineage || lineage, !!previewLineage, lmsPreview)}${lmsPlanCard(plan)}<div class="stat-table-card lms-candidate-card"><div class="table-intro"><p class="eyebrow">Every eligible club</p><p class="subtitle">Select a row to see the horizon that club leads to.</p></div><table><thead><tr><th>Team</th><th>Fixture</th><th>P(Win)</th><th>P(Draw)</th><th>P(Loss)</th><th>Conf</th><th></th></tr></thead><tbody>${lmsRecommendationRows(candidates, !!lmsEntrant, plan?.picks?.[0]?.team)}</tbody></table></div>${lmsEntrant ? "" : `<p class="subtitle">Enter an entrant name above to record a pick.</p>`}`;
  }

  const unknownNote = gw && lms.unknown_teams.length ? `<p class="subtitle">Not in the training corpus, using league-average strength: ${escapeHtml(lms.unknown_teams.join(", "))}.</p>` : "";

  const setupSummary = lms.fixtures_source?.mode === "auto"
    ? `Override gameweek ${gw.number} fixtures`
    : gw ? `Edit gameweek ${gw.number} fixtures` : "Add this gameweek's fixtures manually";
  const setupNote = lms.fixtures_source?.mode === "auto"
    ? `<p class="subtitle">Fetched automatically — any change here is temporary and gets replaced by the next successful fetch.</p>`
    : "";
  const setupSection = `<details class="disclosure"><summary>${setupSummary}</summary><form id="lms-gameweek-form" class="form-card">${setupNote}<label>Gameweek number<input required name="gameweek" type="number" min="1" value="${gw ? gw.number : ""}"></label><label>Fixtures — one per line, "Home vs Away"<textarea required name="fixtures" rows="10" placeholder="Arsenal vs Everton">${gw ? gw.fixtures.map((f) => `${f.home} vs ${f.away}`).join("\n") : ""}</textarea></label><div class="form-actions"><button class="button" type="submit">Save gameweek fixtures</button></div></form></details>`;

  const historySection = `<section class="section-heading"><div><p class="eyebrow">Entrants</p><h2>Survival status</h2></div></section><div class="stat-table-card"><table><thead><tr><th>Entrant</th><th>Status</th><th>Clubs used</th><th>Last result</th></tr></thead><tbody>${lmsHistoryRows(entrants)}</tbody></table></div>`;

  return `<div class="lms-view">${pageHeader("Last Man Standing", "Survivor picks", "Pick one club per gameweek. Win to survive — a draw eliminates too, same as a loss. A club can only be picked once for the whole competition.", '<a class="button secondary-button" href="#lms-replay">Replay a season</a>')}
    ${entrantSection}
    ${lmsFixturesSourceBanner(lms.fixtures_source)}
    ${gw ? lmsAutoSettleBanner(lms.auto_settle) : ""}
    <section class="section-heading"><div><p class="eyebrow">${gw ? `Gameweek ${gw.number}` : "Gameweek"}</p><h2>Planned across the next few gameweeks</h2></div></section>
    ${pickSection}
    ${unknownNote}
    ${setupSection}
    ${historySection}</div>`;
}

function lmsReplayView() {
  const lms = appData.lms || {};
  const lab = lmsReplayLab(lms) || emptyState(
    "No seasons ready to replay",
    "Add at least two completed Premier League seasons to the local data folder, so the opening slate can be forecast without looking ahead."
  );
  return `<div class="lms-view">${pageHeader("Last Man Standing", "Season replay", "Experiment with a completed Premier League season without changing your live LMS competition.", '<a class="button secondary-button" href="#lms">Back to live LMS</a>')}${lab}</div>`;
}

// What you told us you entered, and when. The reconciler already records
// all of this; without it on screen "entry drift" has nothing to drift from.
function submissionRecord(round) {
  const record = round.submission;
  if (!record?.submitted_at && !record?.reviewed_at) return "";
  const picks = (record.submitted_picks || []).map((pick, index) => {
    const fixture = round.fixtures[index];
    const label = fixture ? `${shortTeam(fixture.home_team)}–${shortTeam(fixture.away_team)}` : `Fixture ${index + 1}`;
    const drifted = (record.drift || []).some((change) => change.index === index);
    return `<span class="${drifted ? "drifted" : ""}">${escapeHtml(label)} ${escapeHtml(pick)}</span>`;
  }).join("");
  return `<section class="callout muted submission-record"><strong>Your recorded entry</strong><span>
    ${record.submitted_at ? `Marked as entered ${escapeHtml(displayDate(record.submitted_at))}` : "Not yet marked as entered"}${record.reviewed_at ? ` · last reviewed ${escapeHtml(displayDate(record.reviewed_at))}` : ""}${record.submitted_golden_goal ? ` · Golden Goal minute ${record.submitted_golden_goal}` : ""}.
    <span class="recorded-picks">${picks}</span>
    ${(record.drift || []).length ? `Highlighted picks no longer match the current recommendation.` : `These match the current recommendations.`}
  </span></section>`;
}

function teamPickerField(index, side, value, teams) {
  const known = teams.includes(value);
  const options = teams.map((team) => `<option value="${escapeHtml(team)}" ${team === value ? "selected" : ""}>${escapeHtml(team)}</option>`).join("");
  const label = side === "home" ? "Home" : "Away";
  return `<label><span class="sr-only">Fixture ${index + 1} ${label.toLowerCase()} team</span><select data-team-select data-target="${side}_${index}">${options}<option value="__other__" ${known ? "" : "selected"}>Other (cup game, etc.)</option></select><input required name="${side}_${index}" value="${escapeHtml(value)}" placeholder="${label} team" ${known ? "hidden" : ""}></label>`;
}

function roundSetupView() {
  const { round } = appData;
  const teams = (appData.teams || []).slice().sort();
  return `<section class="form-card"><div class="section-heading"><div><p class="eyebrow">Manual configuration</p><h2>Set up a Super 6 round</h2><p class="subtitle">Sky stays outside the system boundary. Add the six fixtures and confirm the UK deadline here. Pick a team from the list, or choose "Other" for a cup tie or a side the model doesn't know.</p></div></div><form id="round-form"><div class="form-grid"><label>Round number<input required name="round_number" type="number" min="1" value="${round.round_number}"></label><label>Deadline (UK time)<input required name="deadline_at" type="datetime-local" value="${localDateTime(round.deadline_at)}"></label></div><div class="fixture-form-list">${round.fixtures.map((fixture, index) => `<div class="fixture-form-row"><span>${index + 1}</span>${teamPickerField(index, "home", fixture.home_team, teams)}<b>v</b>${teamPickerField(index, "away", fixture.away_team, teams)}</div>`).join("")}</div><div class="form-actions"><button class="button" type="submit">Save round &amp; refresh forecast</button></div></form></section>`;
}

function resultsView(results) {
  const completed = results.fixtures.filter((fixture) => fixture.status === "RESULTED" || fixture.status === "VOID").length;
  return `<section class="results-summary"><div><p class="eyebrow">Round score</p><strong>${results.summary.points} pts</strong><small>${results.summary.exact} exact · ${results.summary.correct} correct result · ${results.summary.void} void</small></div><p>${completed ? `${completed} of 6 fixtures have a recorded outcome.` : "Enter the final outcomes when the round is complete."}</p></section><section class="form-card"><h2>Record results</h2><p class="subtitle">Use <b>VOID</b> for postponed or abandoned fixtures. Void fixtures are excluded from performance, not counted as zero.</p><form id="results-form" class="result-entry-list">${results.fixtures.map((entry, index) => `<label><span>${escapeHtml(entry.fixture.home_team)} v ${escapeHtml(entry.fixture.away_team)}</span><input required name="result_${index}" value="${entry.actual === null ? "VOID" : escapeHtml(entry.actual || "")}" placeholder="e.g. 2-0 or VOID"></label>`).join("")}<label>Actual Golden Goal minute (optional)<input name="golden_goal_actual" type="number" min="1" max="90" value="${results.golden_goal_actual ?? ""}" placeholder="e.g. 11"></label><p class="subtitle">Feeds the private league's Golden Goal tiebreak (§27) — leave blank if the first goal's minute wasn't recorded.</p><div class="form-actions"><button class="button" type="submit">Save round results</button></div></form></section>`;
}

function emptyState(title, copy, action = "") {
  return `<section class="empty-state"><p class="eyebrow">Nothing to review</p><h2>${escapeHtml(title)}</h2><p>${escapeHtml(copy)}</p>${action}</section>`;
}

function seasonLabel(season) {
  return season ? `${season}/${String(season + 1).slice(-2)}` : "";
}

function leagueView() {
  const league = appData.league;
  const name = appData.settings.league_name;
  const season = seasonLabel(league.season);
  if (!league.standings.length) return `${pageHeader(name, "League position", `Add opponent predictions and entered picks, then record final results to build the ${season} private-league table. Every new season starts back at 0-0 automatically.`)}${emptyState("No scored private-league rounds yet", "Opponent selections are kept locally and never collected from Sky.", '<button class="button" data-page="opponents">Import opponent predictions</button>')}`;
  const first = league.finish_first ? percent(league.finish_first.probability) : "Not estimated";
  const seasonHeadline = "League position — " + season + " season";
  return `${pageHeader(name, seasonHeadline, "Every point below is calculated from your recorded picks and final results, scoped to this season only.")}<section class="stat-table-card"><table><thead><tr><th>Rank</th><th>Player</th><th>Points</th><th>Exact</th><th>Golden Goal avg.</th><th>Rounds</th><th>P(Finish 1st)</th></tr></thead><tbody>${league.standings.map((row) => `<tr class="${row.participant === appData.settings.player_name ? "you-row" : ""}"><td>${row.rank}</td><td>${escapeHtml(row.participant)}</td><td>${row.points}</td><td>${row.exact_scores}</td><td>${row.golden_goal_average_distance === null || row.golden_goal_average_distance === undefined ? "—" : `${row.golden_goal_average_distance} min`}</td><td>${row.rounds_scored}</td><td>${row.participant === appData.settings.player_name ? first : "—"}</td></tr>`).join("")}</tbody></table></section><section class="callout muted"><strong>Tiebreak order (§27)</strong><span>Points, then most correct exact scores, then closest average Golden Goal guess. Only a tie surviving all three actually splits.</span></section>${seasonStrategyPanel()}${strategyPanel()}<section class="callout muted"><strong>How P(1st) is estimated</strong><span>${escapeHtml(league.finish_first_reason)}</span></section>`;
}

// §12-13: judges a candidate by its effect on P(Finish 1st) at the end of
// the season — the current round plus a representative sample of the
// unknown rounds still to come (§13.2's Round Opportunity Model) — rather
// than `strategyPanel()` below, which only ever looks at this one round.
function seasonStrategyPanel() {
  const season = appData.league.season_strategy;
  if (!season || !season.mode || !(season.candidates || []).length) return "";
  const rows = season.candidates.map((candidate) => `<tr class="${candidate.recommended ? "recommended" : ""}">
    <td>${candidate.name === "MAX_EV" ? "Maximum EV" : candidate.name === "CORRELATE" ? "Correlate with field" : `Differentiate (${candidate.name.split("_").pop()})`}</td>
    <td>${percent(candidate.finish_first_probability)}</td>
    <td>${candidate.expected_final_points.toFixed(2)}</td></tr>`).join("");
  const state = season.season_state;
  return `<section class="strategy-card season-strategy ${season.active ? "active" : ""}">
    <div class="section-heading"><div><p class="eyebrow">Season strategy — ${escapeHtml(season.mode)}</p>
    <h2>${season.active ? "A season-length simulation favours a different pick" : "Maximum EV already wins the season simulation"}</h2>
    <p class="subtitle">${escapeHtml(season.reason)}</p></div></div>
    ${state ? `<p class="subtitle">Points gap ${state.points_gap} · Exact-score gap ${state.exact_score_gap} · ${state.rounds_remaining} round${state.rounds_remaining === 1 ? "" : "s"} remaining this season</p>` : ""}
    <p class="subtitle">P(Finish 1st) gain over maximum EV: ${season.comparison.mean_gain_pp}pp (95% interval ${season.comparison.lower_bound_pp}pp to ${season.comparison.upper_bound_pp}pp)</p>
    <div class="stat-table-card"><table><thead><tr><th>Approach</th><th>P(finish 1st), season</th><th>Expected final points</th></tr></thead><tbody>${rows}</tbody></table></div>
  </section>`;
}

// The frozen model maximises points for one player alone. A private
// league is a different game: when the field converges on the same
// favourites, shared picks cannot separate you from it. This panel shows
// what deviating costs in points and buys in P(finish first) — it never
// applies anything on its own.
function strategyPanel() {
  const strategy = appData.league.strategy;
  const candidates = strategy.candidates || [];
  if (!candidates.length) {
    return `<section class="callout muted"><strong>Strategy: ${escapeHtml(strategy.mode)}</strong><span>${escapeHtml(strategy.reason)}</span></section>`;
  }
  const best = candidates.find((candidate) => candidate.recommended) || candidates[0];
  const maxEv = candidates.find((candidate) => candidate.name === "MAX_EV");
  const rows = candidates.map((candidate) => `<tr class="${candidate.recommended ? "recommended" : ""}">
    <td>${candidate.name === "MAX_EV" ? "Maximum EV" : `Differentiate (${candidate.name.split("_").pop()})`}</td>
    <td>${percent(candidate.finish_first_probability)}</td>
    <td>${candidate.expected_round_points.toFixed(2)}</td>
    <td class="strategy-detail">${escapeHtml(candidate.description)}</td></tr>`).join("");
  const gain = maxEv ? best.finish_first_probability - maxEv.finish_first_probability : 0;
  const cost = maxEv ? maxEv.expected_round_points - best.expected_round_points : 0;
  return `<section class="strategy-card ${strategy.active ? "active" : ""}">
    <div class="section-heading"><div><p class="eyebrow">Opponent-aware strategy — this round only</p><h2>${strategy.active ? "Deviating improves your chance of winning" : "The maximum-EV picks are already best"}</h2>
    <p class="subtitle">${escapeHtml(strategy.reason)}</p></div>
    ${strategy.active ? `<button class="button" data-action="apply-strategy">Use these picks</button>` : ""}</div>
    ${strategy.active ? `<p class="subtitle strategy-tradeoff"><b>+${(gain * 100).toFixed(1)}pp</b> chance of finishing first, for <b>${cost.toFixed(2)}</b> expected points given up. Winning a league is about finishing above your rivals, not scoring well in isolation — but this is a gamble, and it loses more often than it wins.</p>` : ""}
    <div class="stat-table-card"><table><thead><tr><th>Approach</th><th>P(finish 1st)</th><th>Expected pts</th><th>Deviation</th></tr></thead><tbody>${rows}</tbody></table></div>
  </section>`;
}

function roundKey() {
  return `${new Date(appData.round.deadline_at).getFullYear()}:${appData.round.round_number}`;
}

function opponentBiasLine(participant) {
  const bias = (appData.league.favourite_bias || []).find((item) => item.participant === participant);
  if (!bias || bias.sample_size === 0) return `<small>Favourite bias: not enough joined history yet.</small>`;
  const chase = (appData.league.chase_defend || []).find((item) => item.participant === participant);
  const chaseText = chase && chase.chase_when_behind !== null && chase.chase_when_behind !== undefined
    ? ` · Diverges ${percent(chase.divergence_rate_when_trailing)} while trailing vs ${percent(chase.divergence_rate_when_leading)} while leading`
    : "";
  return `<small>Backs our model's favoured result ${percent(bias.favourite_rate)} of the time (${bias.sample_size} joined picks, influence weight ${percent(bias.influence_weight)})${chaseText}</small>${teamBiasLine(participant)}`;
}

// §11 team-identity bias, stated against what the market priced rather
// than as a raw rate — "backs Man City 80% of the time" would mostly be
// a fact about Man City, not about this rival.
function categoryBiasText(label, category) {
  if (!category || !category.picks || category.bias === null || category.bias === undefined) return "";
  const points = (category.bias * 100).toFixed(0);
  const lean = category.bias >= 0 ? `+${points}pp more` : `${Math.abs(points)}pp less`;
  return `${label} ${lean} than priced (${category.picks} pick${category.picks === 1 ? "" : "s"}, weight ${percent(category.influence_weight)})`;
}

function teamBiasLine(participant) {
  const bias = (appData.league.team_bias || []).find((item) => item.participant === participant);
  if (!bias) return "";
  const parts = [
    categoryBiasText("Big clubs", bias.big_clubs),
    categoryBiasText("Promoted teams", bias.promoted),
  ].filter(Boolean);
  if (!parts.length) return "";
  return `<small title="Compared against the bookmaker odds stored with each forecast, not against our model. Small samples: read as a direction, not a finding.">${escapeHtml(parts.join(" · "))}</small>`;
}

function predictedPicksPanel(participant) {
  const fixtures = (appData.league.predicted_picks || {})[participant];
  if (!fixtures || !fixtures.length) return "";
  const rows = fixtures.map((fixture) => `<div class="predicted-fixture"><span>${escapeHtml(fixture.home_team)} v ${escapeHtml(fixture.away_team)}</span><span>${fixture.predicted.map((item) => `${escapeHtml(item.score)} (${percent(item.probability)})`).join(", ") || "No evidence"}</span></div>`).join("");
  return `<details class="disclosure"><summary>Likely picks this round (§49.3)</summary><p class="subtitle">A heuristic prediction, shrunk toward the league-wide average by how much history exists for this rival — not a validated model.</p>${rows}</details>`;
}

function opponentsView() {
  const profiles = appData.league.profiles || [];
  return `${pageHeader("Opponent modelling", "Your private league", "Import selections supplied through permitted channels. We never collect them from Sky.")}
  <section class="split-layout"><div>${profiles.length ? `<div class="opponent-list">${profiles.map((profile) => `<article class="opponent-card"><h2>${escapeHtml(profile.participant)}</h2><p>${profile.picks} recorded picks · ${profile.rounds} round${profile.rounds === 1 ? "" : "s"}</p><small>Home ${percent(profile.home_result_rate)} · Draw ${percent(profile.draw_result_rate)} · Away ${percent(profile.away_result_rate)} · Avg goals ${profile.average_total_goals} · Conservatism ${percent(profile.conservatism)}</small><small>Common scores: ${profile.common_scores.map(([score, count]) => `${score} (${count})`).join(" · ") || "Not enough history"}</small>${opponentBiasLine(profile.participant)}${predictedPicksPanel(profile.participant)}</article>`).join("")}</div>` : emptyState("No opponent history yet", "Add selections after each deadline. The system will learn only from recorded evidence.")}</div><section class="form-card"><p class="eyebrow">Manual import</p><h2>Import six selections</h2><p class="subtitle">Paste six scores in fixture order, one per line — for example 2-0. You will see the saved picks immediately; no Sky integration is used.</p><form id="opponent-form"><label>Name<input required name="name" maxlength="80" placeholder="e.g. Dave"></label><label>Predictions<textarea required name="picks" rows="7" placeholder="2-0&#10;1-1&#10;2-1&#10;1-2&#10;3-0&#10;1-2"></textarea></label><label>Golden Goal guess (optional)<input name="golden_goal" type="number" min="1" max="90" placeholder="e.g. 9"></label><p class="subtitle">Used for the private league's tiebreak — points, then exact scores, then closest Golden Goal (§27).</p><div class="form-actions"><button class="button" type="submit">Import selections</button></div></form></section></section>`;
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
  return `<div class="model-view">${pageHeader("Expert", "Model Lab", "Technical detail is available here without competing with the weekly decision.")}<section class="callout muted"><strong>Frozen evaluation model</strong><span>${escapeHtml(appData.model.note)} ${escapeHtml(transparency.evaluation_reason || "")}</span></section><section class="spec-grid">${values.map(([label, value]) => `<article><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></article>`).join("")}</section><section class="form-card"><h2>How a pick is selected</h2><p>The scoreline with the highest expected Super 6 value is recommended. Expected points uses the probability of the selected match result plus the probability of the exact score; confidence remains a separate category.</p><p class="subtitle">Open any fixture for its expected-value breakdown, and expand “Score matrix &amp; provenance” there for the full scoreline distribution and source freshness.</p></section></div>`;
}

// §18.1's registry answers three different questions, and showing them as
// one list implied a parity that does not exist: a source nothing queries
// sat beside six the workers depend on, looking equivalent. Group by what
// is actually being said, and tell the user how to act where action is
// theirs to take — a status they cannot resolve and are not told how to
// resolve is a dead end, not governance.
const SOURCE_GROUPS = [
  { key: "in-use", heading: "In use", statuses: ["APPROVED"],
    blurb: "Approved and queried by the always-on workers." },
  { key: "awaiting", heading: "Awaiting your review", statuses: ["REVIEW_REQUIRED"],
    blurb: "Read the terms, then approve from the command line. Nothing queries these until you do." },
  { key: "evaluated", heading: "Evaluated — not in use", statuses: ["EVALUATED"],
    blurb: "Assessed and deliberately not adopted. Recorded so the decision is not re-derived; nothing is pending." },
  { key: "prohibited", heading: "Prohibited", statuses: ["PROHIBITED"],
    blurb: "Cannot be used and cannot be promoted." },
];

function sourceCard(source) {
  const pill = source.usage_status === "APPROVED" ? "approved"
    : source.usage_status === "PROHIBITED" ? "prohibited" : "neutral";
  const checked = source.terms_checked_at
    ? `checked ${escapeHtml(source.terms_checked_at)}`
    : (source.usage_status === "EVALUATED" ? "terms not reviewed — not needed, nothing queries it"
                                           : "terms not reviewed yet");
  const action = source.usage_status === "REVIEW_REQUIRED"
    ? `<code class="source-action">bym sources approve ${escapeHtml(source.source_id)}</code>`
    : "";
  return `<article class="source-card"><div><span class="status-pill ${pill}">${escapeHtml(titleCase(source.usage_status))}</span><h2>${escapeHtml(source.source_name)}</h2><p>${escapeHtml(source.acquisition_method)} · ${checked}</p></div><p>${escapeHtml(source.notes)}</p><small>Purpose: ${escapeHtml((source.purpose || []).join(", ") || "None — nothing queries this source")}</small>${action}${source.terms_url ? `<a href="${escapeHtml(source.terms_url)}" target="_blank" rel="noreferrer">View terms</a>` : ""}</article>`;
}

function sourcesView() {
  const all = appData.sources || [];
  const sections = SOURCE_GROUPS.map((group) => {
    const members = all.filter((s) => group.statuses.includes(s.usage_status));
    if (!members.length) return "";
    return `<section class="source-group"><div class="section-heading"><div><p class="eyebrow">${escapeHtml(group.heading)}</p><p class="subtitle">${escapeHtml(group.blurb)}</p></div></div><div class="source-grid">${members.map(sourceCard).join("")}</div></section>`;
  }).filter(Boolean).join("");
  return `${pageHeader("Evidence governance", "Sources", "Every source remains visible with its approval state, terms review and intended purpose.")}${sections}`;
}

function healthView() {
  const health = appData.health;
  const ollama = health.ollama || {};
  const ollamaLabel = ollama.configured ? health.ollama_reachable : "Not configured";
  const extractorName = String(health.extractor || "").replace(/([a-z])([A-Z])/g, "$1 $2");
  return `<div class="health-view">${pageHeader("Operational status", "System Health", "Health is visible when needed but stays out of the weekly decision flow.")}<section class="health-grid"><article><span>Scheduler</span><strong>${health.scheduler_jobs_pending ? `${health.scheduler_jobs_pending} pending` : "Ready"}</strong></article><article><span>Database</span><strong>Healthy · schema ${health.database_schema_version}</strong></article><article><span>Knowledge extractor</span><strong>${escapeHtml(extractorName)} · ${escapeHtml(health.extractor_mode)}</strong><small>Research runs on a schedule, six sweeps a round.${health.extraction_recent_total ? ` Last ${health.extraction_recent_total} extractions: ${health.extraction_recent_total - health.extraction_recent_failed} ok, ${health.extraction_recent_failed} failed.` : ""}</small>${health.extractor === "NullExtractor" ? "" : `<button class="plain-button" type="button" data-action="research-now">Research now</button>`}</article><article><span>Ollama</span><strong>${escapeHtml(ollamaLabel)}</strong><small>${escapeHtml(ollama.detail || "")}</small></article></section>${health.warnings.length ? `<section class="warning-list">${health.warnings.map((warning) => `<p>${escapeHtml(warning)}</p>`).join("")}</section>` : ""}<section class="stat-table-card"><table><thead><tr><th>Source</th><th>Status</th><th>Quota</th></tr></thead><tbody>${health.sources.map((source) => `<tr><td>${escapeHtml(source.source_id)}</td><td>${escapeHtml(titleCase(source.usage_status))}</td><td>${source.quota_limit ? `${source.quota_used}/${source.quota_limit} per ${escapeHtml(source.quota_period)}` : "Not metered"}</td></tr>`).join("")}</tbody></table></section></div>`;
}

function settingsView() {
  const { settings } = appData;
  const toggles = Object.entries(settings.notifications).map(([key, enabled]) => `<label class="toggle-row"><span>${escapeHtml(titleCase(key))}</span><input type="checkbox" name="notification_${key}" ${enabled ? "checked" : ""}><i></i></label>`).join("");
  const delivery = settings.notification_delivery || { in_app: true, email: false };
  const extractor = settings.knowledge_extractor || { enabled: false, base_url: "http://localhost:11434", model: "llama3.1", mode: "shadow" };
  const models = [...new Set([extractor.model, ...ollamaModels].filter(Boolean))];
  const modelOptions = models.map((model) => `<option value="${escapeHtml(model)}" ${model === extractor.model ? "selected" : ""}>${escapeHtml(model)}</option>`).join("");
  return `${pageHeader("Your preferences", "Settings", "Settings control presentation and alerts. They never change the frozen forecasting core.")}<form id="settings-form" class="settings-grid"><section class="form-card"><p class="eyebrow">League</p><label>League name<input required name="league_name" maxlength="80" value="${escapeHtml(settings.league_name)}"></label><label>Your name<input required name="player_name" maxlength="80" value="${escapeHtml(settings.player_name)}"></label></section><section class="form-card"><p class="eyebrow">Notifications</p>${toggles}<label class="toggle-row"><span>In-app delivery</span><input type="checkbox" name="delivery_in_app" ${delivery.in_app ? "checked" : ""}><i></i></label><label class="toggle-row"><span>Email delivery</span><input type="checkbox" name="delivery_email" ${delivery.email ? "checked" : ""}><i></i></label><label>Email address<input name="notification_email" type="email" value="${escapeHtml(settings.notification_email || "")}" placeholder="you@example.com"></label><p class="subtitle">Email sends material prediction changes through your configured SMTP relay.</p></section><section class="form-card"><p class="eyebrow">Budget</p><label>Target monthly spend<input required name="target_monthly" type="number" min="0" step="1" value="${settings.budget.target_monthly}"></label><label>Hard monthly ceiling<input required name="hard_ceiling" type="number" min="0" step="1" value="${settings.budget.hard_ceiling}"></label><p class="subtitle">Current source spend is not yet connected; limits are saved here for the source-promotion flow.</p></section><section class="form-card"><p class="eyebrow">Season</p><label>Rounds in this Super 6 season<input required name="season_rounds_total" type="number" min="1" max="60" step="1" value="${settings.season_rounds_total || 38}"></label><p class="subtitle">Sky does not publish this in advance, so it stays a manual estimate. It sets how many future rounds the season strategy simulation (§13) looks ahead across.</p></section><section class="form-card"><p class="eyebrow">Knowledge extractor</p><label class="toggle-row"><span>Enable Ollama</span><input type="checkbox" name="extractor_enabled" ${extractor.enabled ? "checked" : ""}><i></i></label><label>Ollama base URL<input required name="extractor_base_url" type="url" maxlength="512" value="${escapeHtml(extractor.base_url)}" placeholder="http://localhost:11434"></label><label>Model<select required name="extractor_model">${modelOptions}</select></label><button class="button secondary-button" type="button" data-action="find-ollama-models">Refresh available models</button><label>Claim handling<select name="extractor_mode"><option value="shadow" ${extractor.mode === "shadow" ? "selected" : ""}>Shadow — record only</option><option value="active" ${extractor.mode === "active" ? "selected" : ""}>Active — promote validated claims</option></select></label><p class="subtitle">Refreshes using the base URL above. Shadow is the safe default; Active still never changes forecast probabilities. A Tavily API key is also required before research can run.</p></section><div class="form-actions"><button class="button" type="submit">Save settings</button></div></form>`;
}

function moreView() {
  const pages = [["Round control", "round", "Changes, setup and results"], ["Opponents", "opponents", "Import picks for your private league"], ["Performance", "performance", "Measure recorded results"], ["Cup Match", "cup", "One-off match forecast"], ["LMS season replay", "lms-replay", "Play through a completed season"], ["Activity", "activity", "Material forecast and intelligence history"], ["Model Lab", "model", "Frozen model and technical detail"], ["Sources", "sources", "Source approval and provenance"], ["System Health", "health", "Operational status and data warnings"], ["Settings", "settings", "League, notification and budget preferences"]];
  return `${pageHeader("More", "More tools", "Everything outside the quick weekly review is still one tap away.")}<div class="more-list">${pages.map(([title, page, copy]) => `<button class="more-link" data-page="${page}"><span><strong>${title}</strong><small>${copy}</small></span><b>›</b></button>`).join("")}</div>`;
}

function renderPage() {
  if (!appData) return;
  const page = (location.hash || "#home").slice(1).split("?")[0] || "home";
  const views = { home: homeView, round: roundView, cup: cupView, lms: lmsView, "lms-replay": lmsReplayView, league: leagueView, opponents: opponentsView, performance: performanceView, activity: activityView, model: modelView, sources: sourcesView, health: healthView, settings: settingsView, more: moreView };
  $("#app").innerHTML = (views[page] || homeView)();
  document.querySelectorAll("[data-page-link]").forEach((link) => link.classList.toggle("active", link.dataset.pageLink === page));
  markScrollableRegions();
  updateCountdown();
}

// WCAG 2.1.1 (Keyboard, level A). The stat tables have a min-width, so on a
// narrow viewport their card scrolls sideways — and a mouse or trackpad was
// the only way to scroll it. A scrollable region has to be reachable by
// keyboard, which means a tab stop.
//
// Applied only to cards that actually overflow, and re-evaluated on resize:
// giving every table a permanent tab stop would add stops that lead
// nowhere at desktop width, trading one keyboard problem for another. The
// accessible name matters too — landing on an unnamed scroll box tells a
// screen-reader user nothing about where they are.
function markScrollableRegions() {
  document.querySelectorAll(".stat-table-card").forEach((card) => {
    const scrolls = card.scrollWidth > card.clientWidth;
    if (scrolls) {
      card.setAttribute("tabindex", "0");
      card.setAttribute("role", "region");
      if (!card.hasAttribute("aria-label")) {
        const heading = card.querySelector("caption, th");
        card.setAttribute(
          "aria-label",
          heading ? `${heading.textContent.trim()} table, scrollable` : "Scrollable table"
        );
      }
    } else {
      card.removeAttribute("tabindex");
      card.removeAttribute("role");
    }
  });
}

window.addEventListener("resize", markScrollableRegions);

// Movement and divergence describe the market, never the forecast. The
// frozen model already consumed this consensus through its blend, so the
// copy must not imply these figures adjust the pick a second time.
// Returns plain text — `last_movement` embeds team names from the round
// file, so callers must escape at the render site.
function marketNotes(fixture) {
  const market = fixture.market;
  if (!market?.available) return [];
  const notes = [];
  if (market.last_movement) {
    const agreement = market.market_agreement ? `, books ${market.market_agreement.toLowerCase()} agreement` : "";
    notes.push(`Market moved since the previous poll — ${market.last_movement}${market.materiality ? ` (${market.materiality.toLowerCase()}${agreement})` : ""}. The forecast above already uses the newer price.`);
  } else if (market.bookmaker_count) {
    notes.push(`No movement recorded against an earlier poll for this fixture.`);
  }
  if (market.model_vs_market !== null && market.model_vs_market !== undefined) {
    const divergence = market.market_divergence;
    const gloss = divergence === "HIGH"
      ? "the model disagrees with a market that is itself confident — treat the pick with extra caution"
      : divergence === "MEDIUM"
        ? "a moderate disagreement between model and market"
        : "model and market broadly agree";
    notes.push(`Model vs market divergence: ${divergence} — ${gloss}. Total variation ${market.model_vs_market.toFixed(3)}.`);
  }
  if (market.stale) notes.push("The market view is stale — every quote fell outside the freshness tolerance.");
  return notes;
}

function reasonFor(fixture) {
  const [homeGoals, awayGoals] = fixture.pick.split("-").map(Number);
  const result = homeGoals === awayGoals ? "draw" : homeGoals > awayGoals ? `${fixture.home_team} win` : `${fixture.away_team} win`;
  const market = fixture.market?.available ? `The available market view assigns ${percent(fixture.market.home_probability)} to a home win, ${percent(fixture.market.draw_probability)} to a draw, and ${percent(fixture.market.away_probability)} to an away win.` : "No current market view is available, so this recommendation relies on the historical model only.";
  return { result, market };
}

// UIUX §41.2 asks for a heatmap. The matrix runs to max_goals (11x11), but
// the tail is visually dead weight, so the grid is capped and the caption
// states how much probability is actually on screen rather than implying
// the whole distribution is shown.
const HEATMAP_CAP = 6;

function heatmapGrid(explanation) {
  const heatmap = explanation?.heatmap;
  if (!heatmap?.cells?.length) return "";
  const shown = heatmap.cells.filter((cell) => cell.home_goals <= HEATMAP_CAP && cell.away_goals <= HEATMAP_CAP);
  const captured = shown.reduce((total, cell) => total + cell.probability, 0);
  const peak = Math.max(...shown.map((cell) => cell.probability));
  const at = new Map(shown.map((cell) => [`${cell.home_goals}-${cell.away_goals}`, cell]));
  const range = Array.from({ length: HEATMAP_CAP + 1 }, (_, i) => i);

  const header = range.map((away) => `<th scope="col">${away}</th>`).join("");
  const rows = range.map((home) => {
    const cells = range.map((away) => {
      const cell = at.get(`${home}-${away}`);
      if (!cell) return `<td></td>`;
      const intensity = peak > 0 ? (cell.probability / peak).toFixed(3) : 0;
      return `<td class="${cell.selected ? "selected" : ""}" style="--intensity:${intensity}" title="${escapeHtml(cell.score)} — ${percent(cell.probability)}, EV ${cell.expected_points.toFixed(2)}"><span>${(cell.probability * 100).toFixed(1)}</span></td>`;
    }).join("");
    return `<tr><th scope="row">${home}</th>${cells}</tr>`;
  }).join("");

  return `<section class="heatmap-block"><p class="eyebrow">Score matrix</p>
    <p class="subtitle">Probability of each scoreline, as a percentage. ${escapeHtml(heatmap.home_team)} goals down the side, ${escapeHtml(heatmap.away_team)} across the top. The recommended score is outlined.</p>
    <div class="heatmap-scroll"><table class="heatmap"><caption class="sr-only">Scoreline probability matrix</caption><thead><tr><th scope="col"><span class="sr-only">Home goals versus away goals</span>⁄</th>${header}</tr></thead><tbody>${rows}</tbody></table></div>
    <p class="subtitle">Showing 0–${HEATMAP_CAP} goals per side: ${percent(captured)} of all outcomes. The remaining ${percent(1 - captured)} sits in higher scorelines, which are in the model but not drawn here.</p></section>`;
}

// A confirmed XI departing materially from the expected one. Raised as an
// event by the availability layer, and a standing fact on the read model —
// which is what a fixture opened after the fact needs.
function surpriseNote(fixture) {
  const surprise = fixture.availability?.lineup_surprise;
  if (!surprise) return "";
  return `<div class="surprise-note"><b>Lineup surprise</b> ${escapeHtml(surprise)}. Quantitative impact on this forecast: none directly.</div>`;
}

function evBreakdown(explanation) {
  const pick = explanation?.selected_pick;
  if (!pick) return "";
  return `<section class="ev-block"><p class="eyebrow">How this expected value is built</p>
    <p class="formula">${escapeHtml(pick.formula)}</p>
    <table class="ev-table"><tbody>
      <tr><td>P(result)</td><td>${percent(pick.result_probability)}</td><td>× ${pick.result_points} pts</td><td><strong>${pick.result_expected_points.toFixed(3)}</strong></td></tr>
      <tr><td>P(exact ${escapeHtml(pick.score)})</td><td>${percent(pick.exact_score_probability)}</td><td>× ${pick.exact_score_bonus_points} pts</td><td><strong>${pick.exact_score_bonus_expected_points.toFixed(3)}</strong></td></tr>
      <tr class="total"><td colspan="3">Expected Super 6 points</td><td><strong>${pick.expected_points.toFixed(3)}</strong></td></tr>
    </tbody></table></section>`;
}

function sourceFreshness(explanation) {
  const provenance = explanation?.provenance;
  if (!provenance) return "";
  const sources = (provenance.sources || []).map((source) => `<li><b>${escapeHtml(titleCase(source.source_id))}</b> — ${source.available ? escapeHtml(source.freshness) : "unavailable"}${source.detail ? `: ${escapeHtml(source.detail)}` : ""}${source.observed_at ? ` (observed ${escapeHtml(displayDate(source.observed_at))})` : ""}</li>`).join("");
  const inputs = provenance.model_inputs || {};
  return `<section class="provenance-block"><p class="eyebrow">Provenance</p>
    <ul>${sources || "<li>No source provenance recorded for this forecast.</li>"}</ul>
    <p class="subtitle">Generated ${escapeHtml(displayDate(provenance.generated_at))} by ${escapeHtml(provenance.model_version)} — expected goals ${Number(inputs.lambda_home).toFixed(2)} v ${Number(inputs.lambda_away).toFixed(2)}, ρ ${Number(inputs.rho).toFixed(3)}, grid capped at ${inputs.max_goals}.</p></section>`;
}

function openFixture(index) {
  const fixture = appData.round.fixtures[index];
  const reason = reasonFor(fixture);
  const alternatives = fixture.alternatives.map((alternative) => `<tr class="${alternative.is_recommendation ? "recommended" : ""}"><td>${alternative.score}${alternative.is_recommendation ? " · PICK" : ""}</td><td>${percent(alternative.score_probability)}</td><td>${alternative.expected_points.toFixed(2)}</td></tr>`).join("");
  const evidence = fixture.availability?.items?.length ? fixture.availability.items.map((item) => `<li>${escapeHtml(item.player_name)} — ${escapeHtml(titleCase(item.status))}; ${escapeHtml(item.certainty)} via ${escapeHtml(item.source)}.</li>`).join("") : `<li>No individual team-news item is recorded for this fixture.</li>`;
  // §19/§43: each entry is a reconstructable forecast_run — what the pick
  // was, and what it was based on (model vs market-only fallback, plus the
  // market/availability state consumed at that moment, not the current one).
  const history = fixture.history?.length ? fixture.history.map((version) => {
    const inputs = [];
    if (version.basis && version.basis !== "PHASE1_MODEL_V1") inputs.push(`basis ${version.basis}`);
    if (version.market_snapshot) inputs.push(`market ${percent(version.market_snapshot.home_probability)}/${percent(version.market_snapshot.draw_probability)}/${percent(version.market_snapshot.away_probability)} (${version.market_snapshot.bookmaker_count} books)`);
    if (version.availability_snapshot) inputs.push(`availability ${version.availability_snapshot.data_quality}, ${version.availability_snapshot.item_count} item${version.availability_snapshot.item_count === 1 ? "" : "s"}`);
    const detail = inputs.length ? ` <small>(${inputs.map(escapeHtml).join(" · ")})</small>` : "";
    return `<li>${escapeHtml(displayDate(version.recorded_at))}: ${escapeHtml(version.pick)} — ${escapeHtml(version.reason || "Forecast recorded")}${detail}</li>`;
  }).join("") : `<li>No earlier forecast version is persisted yet.</li>`;
  const [home, away] = effectivePick(fixture).split("-");
  const unmodellable = hasNoForecast(fixture);
  const unmodellableBanner = unmodellable
    ? `<section class="danger-card"><p class="eyebrow">No recommendation</p><h3>The model cannot forecast this fixture.</h3><p>${escapeHtml(modellabilityNote(fixture.modellability))}</p><p class="subtitle">Add odds for this fixture and a market-based pick becomes available. The figures below are raw model output, kept visible so the failure can be checked. That does not make them usable.</p></section>`
    : isMarketOnly(fixture)
      ? `<section class="notice-card"><p class="eyebrow">Market-derived pick</p><h3>This forecast comes from the market, not the model.</h3><p>${escapeHtml(marketOnlyNote(fixture))}</p></section>`
      : "";
  // Leading with the scoreline would present the fallback as a recommendation,
  // which is the exact failure the banner underneath is there to prevent.
  const headlineScore = unmodellable ? "—" : escapeHtml(effectivePick(fixture).replace("-", " – "));
  const headlineNote = unmodellable
    ? `Model returned ${escapeHtml(fixture.pick)} from the league-average prior${fixture.user_pick ? ` · your pick ${escapeHtml(fixture.user_pick)}` : ""}`
    : `Model ${escapeHtml(fixture.pick)}${fixture.user_pick ? ` · your pick ${escapeHtml(fixture.user_pick)}` : ""}`;
  const overrideForm = `<form id="override-form" data-index="${index}" class="inline-score-form"><p class="eyebrow">Your pick</p><label>Home goals<input aria-label="${escapeHtml(fixture.home_team)} goals" name="home" min="0" max="20" type="number" value="${home}"></label><span>–</span><label>Away goals<input aria-label="${escapeHtml(fixture.away_team)} goals" name="away" min="0" max="20" type="number" value="${away}"></label><button class="button" type="submit">Use this score</button></form>`;
  $("#fixture-detail").innerHTML = `<div class="detail-header"><p class="eyebrow">Fixture recommendation</p><div class="detail-teams"><span>${teamBadge(fixture.home_team, "large")}${escapeHtml(fixture.home_team)}</span><b>v</b><span>${teamBadge(fixture.away_team, "large")}${escapeHtml(fixture.away_team)}</span></div><div class="detail-score ${unmodellable ? "no-pick" : ""}">${headlineScore}</div><p class="subtitle">${headlineNote}</p></div>${unmodellableBanner}<section class="fixture-decision"><div><p class="eyebrow">${unmodellable ? "Your decision" : "Recommended score"}</p><strong>${headlineScore}</strong><span>${unmodellable ? "Choose a score yourself" : `${confidenceMeaning(fixture)} · ${fixture.confidence} confidence`}</span></div>${overrideForm}</section><div class="detail-grid"><div class="detail-metric"><span>Result probability</span><strong>${percent(fixture.result_probability)}</strong></div><div class="detail-metric"><span>Exact-score probability</span><strong>${percent(fixture.score_probability)}</strong></div><div class="detail-metric"><span>Expected Super 6 points</span><strong>${fixture.expected_points.toFixed(2)}</strong></div><div class="detail-metric"><span>Confidence</span><strong class="confidence ${unmodellable ? "" : fixture.confidence.toLowerCase()}">${unmodellable ? `${fixture.confidence} — of the wrong distribution` : `${confidenceMeaning(fixture)} · ${fixture.confidence}`}</strong></div></div><section class="why-card"><p class="eyebrow">${unmodellable ? `Why no pick?` : `Why ${fixture.pick}?`}</p><h3>${unmodellable ? "The distribution below is not about these two teams." : `${escapeHtml(reason.result)} is the highest-value result.`}</h3><p>${unmodellable ? "It is the highest-expected-value score under a distribution built from a league-average stand-in, so it describes an average team, not the one actually playing." : "This score produces the highest expected Super 6 score from the current probability distribution. It is a recommendation, not a certainty."}</p><ul><li>${escapeHtml(reason.market)}</li>${marketNotes(fixture).map((note) => `<li>${escapeHtml(note)}</li>`).join("")}<li>Data quality: ${escapeHtml(fixture.availability?.data_quality || "LIMITED")}.</li><li>Confidence is separate from both probability figures.</li></ul></section><details class="disclosure"><summary>Alternatives &amp; trade-offs</summary><section class="alternatives"><table><thead><tr><th>Score</th><th>Score probability</th><th>Super 6 EV</th></tr></thead><tbody>${alternatives}</tbody></table>${evBreakdown(fixture.explanation)}</section></details><details class="disclosure"><summary>Score matrix &amp; provenance</summary>${heatmapGrid(fixture.explanation)}${sourceFreshness(fixture.explanation)}</details><details class="disclosure"><summary>Evidence &amp; prediction history</summary>${surpriseNote(fixture)}<h3>Current evidence</h3><ul>${evidence}</ul><h3>Prediction history</h3><ul>${history}</ul></details>`;
  $("#fixture-dialog").showModal();
}

function openInfo(title, content) {
  $("#info-detail").innerHTML = `<p class="eyebrow">How to read this</p><h2>${escapeHtml(title)}</h2><p class="subtitle">${escapeHtml(content)}</p>`;
  $("#info-dialog").showModal();
}

function openEntryDialog() {
  const round = appData.round;
  const rows = round.fixtures.map((fixture) => `<div><span>${shortTeam(fixture.home_team)} ${escapeHtml(fixture.home_team)} v ${escapeHtml(fixture.away_team)} ${shortTeam(fixture.away_team)}</span><strong>${effectivePick(fixture)}</strong>${hasNoForecast(fixture) ? '<em class="entry-flag">no forecast</em>' : isMarketOnly(fixture) ? '<em class="entry-flag market">market</em>' : ""}</div>`).join("");
  const blank = round.fixtures.filter(hasNoForecast);
  const marketOnly = round.fixtures.filter(isMarketOnly);
  const label = (fixture) => `${fixture.home_team} v ${fixture.away_team}`;
  const unmodellableWarning = [
    blank.length ? `<section class="danger-card"><h3>${blank.length} fixture${blank.length === 1 ? " has" : "s have"} no forecast at all.</h3><p>${escapeHtml(blank.map(label).join("; "))} — the score shown is raw model output for a team outside the training corpus, not a recommendation. Choose it yourself before recording this entry.</p></section>` : "",
    marketOnly.length ? `<section class="notice-card"><h3>${marketOnly.length} pick${marketOnly.length === 1 ? " is" : "s are"} market-derived.</h3><p>${escapeHtml(marketOnly.map(label).join("; "))} — taken from the odds you entered, not from the model. Usable, but worth your own sanity check.</p></section>` : "",
  ].join("");
  $("#entry-detail").innerHTML = `<p class="eyebrow">Manual entry confirmation</p><h2>Have you entered these in Super 6?</h2><p class="subtitle">This only records your confirmation. It does not submit or update anything on Sky.</p>${unmodellableWarning}<div class="entry-list">${rows}<div><span>Golden Goal</span><strong>Minute ${round.golden_goal.minute}</strong></div></div><div class="dialog-actions"><button class="plain-button" data-close="entry-dialog">Not yet</button><button class="button" id="confirm-entered">Yes — mark as entered</button></div>`;
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

async function loadLmsReplay(season, game, usedClubs = [], suggestedUsedClubs = []) {
  const data = await post("/api/lms/replay", {
    season, game, used_clubs: usedClubs, suggested_used_clubs: suggestedUsedClubs,
  });
  lmsReplay = {
    ...lmsReplay,
    data,
    season,
    usedClubs,
    suggestedUsedClubs,
    selectedTeam: "",
    revealed: false,
    eliminated: false,
  };
  renderPage();
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
  const initialLoad = !appData;
  const loading = $("#loading");
  const loadingCopy = $("#loading-copy");
  if (initialLoad) loading.hidden = false;
  appLoadController?.abort();
  appLoadController = new AbortController();
  const slowNotice = setTimeout(() => {
    loadingCopy.textContent = "Building the local forecast model. The first load after a restart can take a few seconds.";
  }, 2500);
  const timeout = setTimeout(() => appLoadController.abort(), 30000);
  try {
    const response = await fetch("/api/app", { cache: "no-store", signal: appLoadController.signal });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not build the decision centre.");
    appData = payload;
    clearInterval(countdownTimer);
    countdownTimer = setInterval(updateCountdown, 30_000);
    loading.hidden = true;
    $("#app").hidden = false;
    renderPage();
  } catch (error) {
    loading.hidden = true;
    const message = error.name === "AbortError"
      ? "The forecast took longer than 30 seconds to load. Check that the local service is running, then try again."
      : `The decision centre could not load: ${error.message}`;
    $("#error").innerHTML = `<p>${escapeHtml(message)}</p><button class="button" data-action="retry-load">Try again</button>`;
    $("#error").hidden = false;
  } finally {
    clearTimeout(slowNotice);
    clearTimeout(timeout);
    appLoadController = undefined;
  }
}

window.addEventListener("hashchange", renderPage);
document.addEventListener("change", (event) => {
  const select = event.target.closest("[data-team-select]");
  if (select) {
    const target = select.form?.elements[select.dataset.target];
    if (!target) return;
    if (select.value === "__other__") {
      target.hidden = false;
      target.value = "";
      target.focus();
    } else {
      target.value = select.value;
      target.hidden = true;
    }
    return;
  }
  if (event.target.id === "lms-entrant-input") {
    lmsEntrant = event.target.value.trim();
    lmsPreview = "";
    lmsGraphWeek = "";
    renderPage();
  }
});
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
  if (action === "retry-load") { loadApp(); return; }
  if (action === "mark-entered") openEntryDialog();
  if (action === "research-now") {
    const button = event.target.closest("[data-action]");
    button.disabled = true;
    const previous = button.textContent;
    button.textContent = "Researching…";
    try {
      const result = await post("/api/research", {});
      toast(result.message);
      await loadApp();
    } catch (error) {
      // Evidence is stored independently of extraction, so caching the
      // articles now and extracting later is a real option rather than a
      // way to waste credits twice.
      if (/gather evidence anyway/.test(error.message)
          && confirm(`${error.message}\n\nGather evidence anyway? The articles are cached and can be re-extracted later.`)) {
        try {
          const forced = await post("/api/research", { force: true });
          toast(forced.message);
          await loadApp();
        } catch (retryError) { toast(retryError.message); }
      } else {
        toast(error.message);
      }
    } finally {
      button.disabled = false;
      button.textContent = previous;
    }
  }
  if (action === "find-ollama-models") {
    try {
      const form = $("#settings-form");
      const result = await post("/api/ollama/models", { base_url: form.elements.extractor_base_url.value });
      ollamaModels = result.models || [];
      const select = form.elements.extractor_model;
      const selected = select.value;
      const models = [...new Set([selected, ...ollamaModels].filter(Boolean))];
      select.innerHTML = models.map((model) => `<option value="${escapeHtml(model)}" ${model === selected ? "selected" : ""}>${escapeHtml(model)}</option>`).join("");
      toast(ollamaModels.length ? `${ollamaModels.length} model${ollamaModels.length === 1 ? "" : "s"} found. Choose one from the Model menu.` : "No models found on this Ollama host.");
    } catch (error) { toast(error.message); }
  }
  if (action === "apply-strategy") {
    const picks = appData.league.strategy.picks;
    if (picks?.length) {
      try {
        await saveOverrides(picks);
        toast("Strategy picks saved as your picks. The model recommendation is unchanged underneath.");
      } catch (error) { toast(error.message); }
    }
  }
  if (action === "cup-extended" && cupForecast) {
    try {
      cupForecast = await post("/api/cup-prediction", { home: cupForecast.fixture.home_team, away: cupForecast.fixture.away_team, extended: true });
      renderPage();
    } catch (error) { toast(error.message); }
  }
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
  const graphWeek = event.target.closest("[data-lms-graph-week]");
  if (graphWeek) {
    lmsGraphWeek = graphWeek.dataset.lmsGraphWeek;
    renderPage();
    return;
  }
  const previewClear = event.target.closest("[data-lms-preview-clear]");
  if (previewClear) { lmsPreview = ""; renderPage(); return; }
  const previewRow = event.target.closest("[data-lms-preview]");
  // The row toggles the preview; the button inside it commits. Checked
  // before the pick handler so a click on the button is not also read as
  // a request to preview the row it sits in.
  if (previewRow && !event.target.closest("[data-lms-pick]")) {
    lmsPreview = lmsPreview === previewRow.dataset.team ? "" : previewRow.dataset.team;
    renderPage();
    return;
  }
  const lmsPick = event.target.closest("[data-lms-pick]");
  if (lmsPick) {
    if (!lmsEntrant) { toast("Enter an entrant name first."); return; }
    try {
      appData.lms = await post("/api/lms/pick", {
        entrant: lmsEntrant,
        gameweek: appData.lms.decision_weeks?.[lmsEntrant] ?? appData.lms.gameweek.number,
        team: lmsPick.dataset.team, opponent: lmsPick.dataset.opponent,
        is_home: lmsPick.dataset.home === "true",
      });
      toast(`${lmsPick.dataset.team} recorded as ${lmsEntrant}'s pick for gameweek ${appData.lms.gameweek.number}.`);
      lmsPreview = "";
      renderPage();
    } catch (error) { toast(error.message); }
  }
  const lmsResult = event.target.closest("[data-lms-result]");
  if (lmsResult) {
    try {
      appData.lms = await post("/api/lms/result", {
        entrant: lmsEntrant, gameweek: appData.lms.gameweek.number,
        result: lmsResult.dataset.lmsResult,
      });
      toast(`Recorded ${lmsResult.dataset.lmsResult} for ${lmsEntrant}.`);
      renderPage();
    } catch (error) { toast(error.message); }
  }
  const replayPick = event.target.closest("[data-lms-replay-pick]");
  if (replayPick && !lmsReplay.revealed) {
    lmsReplay.selectedTeam = replayPick.dataset.lmsReplayPick;
    renderPage();
    return;
  }
  if (event.target.closest("[data-lms-replay-reveal]")) {
    const picked = lmsReplay.data?.candidates.find((candidate) => candidate.team === lmsReplay.selectedTeam);
    if (!picked) { toast("Choose a club before revealing the result."); return; }
    lmsReplay.revealed = true;
    lmsReplay.eliminated = !picked.won;
    const game = lmsReplay.data.game ?? lmsReplay.data.slate;
    lmsReplay.history = [...lmsReplay.history, {
      game, team: picked.team, pWin: picked.p_win, won: picked.won,
    }];
    if (picked.won) lmsReplay.usedClubs = [...lmsReplay.usedClubs, picked.team];
    if (!lmsReplay.suggestedEliminated && replaySuggestedPick(lmsReplay.data)) {
      const suggested = replaySuggestedPick(lmsReplay.data);
      const won = replaySuggestedWon(lmsReplay.data, suggested);
      lmsReplay.suggestedHistory = [...lmsReplay.suggestedHistory, {
        game, team: suggested?.team || lmsReplay.data.suggested_pick, pWin: suggested?.p_win, won,
      }];
      if (won) lmsReplay.suggestedUsedClubs = [...lmsReplay.suggestedUsedClubs, suggested?.team || lmsReplay.data.suggested_pick];
      else lmsReplay.suggestedEliminated = true;
    }
    renderPage();
    return;
  }
  if (event.target.closest("[data-lms-replay-next]")) {
    try {
      await loadLmsReplay(
        lmsReplay.season,
        (lmsReplay.data.game ?? lmsReplay.data.slate) + 1,
        lmsReplay.usedClubs,
        lmsReplay.suggestedUsedClubs,
      );
    } catch (error) { toast(error.message); }
    return;
  }
  if (event.target.closest("[data-lms-replay-reset]")) {
    lmsReplay = { data: null, season: lmsReplay.season, usedClubs: [], suggestedUsedClubs: [], history: [], suggestedHistory: [], selectedTeam: "", revealed: false, eliminated: false, suggestedEliminated: false };
    renderPage();
  }
  if (event.target.closest("[data-lms-replay-path]")) openLmsReplayPath();
  if (event.target.closest("[data-lms-live-path]") && lmsEntrant) {
    const state = appData.lms?.entrants?.[lmsEntrant];
    if (state) openLiveLmsPath(lmsEntrant, state);
  }
});

// The labelled week buttons above the graph provide the primary keyboard
// route. The chart's larger hit areas mirror that interaction for people
// exploring the plotted line directly.
document.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const graphWeek = event.target.closest?.("rect[data-lms-graph-week]");
  if (!graphWeek) return;
  event.preventDefault();
  lmsGraphWeek = graphWeek.dataset.lmsGraphWeek;
  renderPage();
});

document.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  try {
    if (form.id === "override-form") {
      const picks = appData.round.fixtures.map(effectivePick);
      picks[Number(form.dataset.index)] = `${form.elements.home.value}-${form.elements.away.value}`;
      await saveOverrides(picks); $("#fixture-dialog").close(); toast("Your pick is saved separately from the model recommendation.");
    } else if (form.id === "cup-form") {
      cupForecast = await post("/api/cup-prediction", { home: form.elements.home.value, away: form.elements.away.value, extended: form.elements.extended.checked });
      renderPage();
    } else if (form.id === "round-form") {
      const fixtures = Array.from({ length: 6 }, (_, index) => ({ home: form.elements[`home_${index}`].value.trim(), away: form.elements[`away_${index}`].value.trim() }));
      openRoundConfirm({ round_number: Number(form.elements.round_number.value), deadline_at: `${form.elements.deadline_at.value}:00`, fixtures });
    } else if (form.id === "opponent-form") {
      const picks = form.elements.picks.value.trim().split(/[\s,]+/).filter(Boolean);
      await post("/api/opponents", { name: form.elements.name.value, picks, golden_goal: form.elements.golden_goal.value }); toast("Opponent selections imported."); await loadApp();
    } else if (form.id === "results-form") {
      const results = Array.from({ length: 6 }, (_, index) => { const value = form.elements[`result_${index}`].value.trim().toUpperCase(); return value === "VOID" ? null : value; });
      await post("/api/results", { results, golden_goal_actual: form.elements.golden_goal_actual.value }); toast("Round results saved."); await loadApp();
    } else if (form.id === "settings-form") {
      const notifications = Object.keys(appData.settings.notifications).reduce((out, key) => ({ ...out, [key]: form.elements[`notification_${key}`].checked }), {});
      await post("/api/settings", { league_name: form.elements.league_name.value, player_name: form.elements.player_name.value, notifications, notification_delivery: { in_app: form.elements.delivery_in_app.checked, email: form.elements.delivery_email.checked }, notification_email: form.elements.notification_email.value, budget: { target_monthly: form.elements.target_monthly.value, hard_ceiling: form.elements.hard_ceiling.value }, season_rounds_total: form.elements.season_rounds_total.value, knowledge_extractor: { enabled: form.elements.extractor_enabled.checked, base_url: form.elements.extractor_base_url.value, model: form.elements.extractor_model.value, mode: form.elements.extractor_mode.value } });
      toast("Settings saved."); await loadApp();
    } else if (form.id === "lms-gameweek-form") {
      const fixtures = form.elements.fixtures.value.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
        const [home, away] = line.split(/\s+vs\s+/i);
        if (!home || !away) throw new Error(`Could not parse fixture "${line}" — use "Home vs Away".`);
        return { home: home.trim(), away: away.trim() };
      });
      appData.lms = await post("/api/lms/gameweek", { gameweek: Number(form.elements.gameweek.value), fixtures });
      toast("Gameweek fixtures saved.");
      renderPage();
    } else if (form.id === "lms-replay-form") {
      const season = Number(form.elements.season.value);
      lmsReplay = { data: null, season, usedClubs: [], suggestedUsedClubs: [], history: [], suggestedHistory: [], selectedTeam: "", revealed: false, eliminated: false, suggestedEliminated: false };
      await loadLmsReplay(season, 1);
    }
  } catch (error) { toast(error.message); }
});

loadApp();

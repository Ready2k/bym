/**
 * UI logic tests. The views build HTML by string interpolation, so the
 * tests that matter most are the ones proving user-controlled text cannot
 * escape its slot, and that the three forecast states stay distinguishable.
 */

import test from "node:test";
import assert from "node:assert/strict";

import { loadApp, appData, fixture } from "./harness.mjs";

const HOSTILE = '<img src=x onerror="alert(1)">';

// ---- escaping ------------------------------------------------------------

test("escapeHtml neutralises every character that can break out of markup", () => {
  const app = loadApp(appData());
  assert.equal(app.escapeHtml(`<>&"'`), "&lt;&gt;&amp;&quot;&#039;");
  assert.equal(app.escapeHtml(null), "");
  assert.equal(app.escapeHtml(undefined), "");
  assert.equal(app.escapeHtml(0), "0");
});

test("a hostile team name cannot inject markup into a fixture card", () => {
  const app = loadApp(appData());
  const html = app.fixtureCard(fixture({ home_team: HOSTILE }), 0);
  assert.ok(!html.includes("<img src=x"), "raw tag reached the DOM string");
  assert.ok(html.includes("&lt;img"), "the name should still be displayed, escaped");
});

test("a hostile team name is escaped in every forecast state", () => {
  const app = loadApp(appData());
  const states = [
    fixture({ home_team: HOSTILE }),
    fixture({
      home_team: HOSTILE, forecast_basis: "NONE",
      modellability: { modellable: false, unknown_teams: [HOSTILE], corpus: "E0+E1", reason: "" },
    }),
    fixture({
      home_team: HOSTILE, forecast_basis: "MARKET_ONLY_FALLBACK",
      modellability: { modellable: false, unknown_teams: [HOSTILE], corpus: "E0+E1", reason: "" },
    }),
  ];
  for (const [index, state] of states.entries()) {
    const html = app.fixtureCard(state, 0);
    assert.ok(!html.includes("<img src=x"), `state ${index} leaked raw markup`);
  }
});

test("plain-text note builders are escaped by their callers, not themselves", () => {
  // marketNotes and modellabilityNote deliberately return unescaped text so
  // callers escape once. This pins the contract: the raw builder carries the
  // hostile text through, and the rendered card does not.
  const app = loadApp(appData());
  const hostile = fixture({
    home_team: HOSTILE,
    modellability: { modellable: false, unknown_teams: [HOSTILE], corpus: "E0+E1", reason: "" },
    forecast_basis: "NONE",
  });
  assert.ok(app.modellabilityNote(hostile.modellability).includes("<img src=x"),
    "builder should return plain text");
  assert.ok(!app.fixtureCard(hostile, 0).includes("<img src=x"),
    "the card must escape what the builder returned");
});

test("a hostile opponent name cannot inject markup into the league view", () => {
  const app = loadApp(appData({
    league: {
      standings: [{ rank: 1, participant: HOSTILE, points: 3, exact_scores: 0, rounds_scored: 1 }],
      profiles: [], finish_first: null, finish_first_reason: "r",
      strategy: { mode: "NORMAL", active: false, reason: "r" },
    },
  }));
  const html = app.leagueView();
  assert.ok(!html.includes("<img src=x"));
  assert.ok(html.includes("&lt;img"));
});

// ---- forecast basis ------------------------------------------------------

test("the three forecast bases are visually distinct", () => {
  const app = loadApp(appData());
  const unmodellable = {
    modellable: false, unknown_teams: ["Oldham"], corpus: "E0+E1", reason: "",
  };

  const normal = app.fixtureCard(fixture(), 0);
  const market = app.fixtureCard(fixture({
    forecast_basis: "MARKET_ONLY_FALLBACK", modellability: unmodellable,
  }), 0);
  const none = app.fixtureCard(fixture({
    forecast_basis: "NONE", modellability: unmodellable,
  }), 0);

  assert.ok(normal.includes("Model pick:"), "normal card should name the model");
  assert.ok(market.includes("Market pick:"), "market fallback should say so");
  assert.ok(market.includes("Not PHASE1_MODEL_V1"));
  assert.ok(none.includes("NO PICK"), "unpriced fixture must withhold the pick");
  assert.ok(!none.includes("Model pick:"));
});

test("an unpriced out-of-corpus fixture withholds the scoreline entirely", () => {
  const app = loadApp(appData());
  const html = app.fixtureCard(fixture({
    pick: "2-0", forecast_basis: "NONE",
    modellability: { modellable: false, unknown_teams: ["Salford"], corpus: "E0+E1", reason: "" },
  }), 0);
  assert.ok(!/<strong>2<\/strong>/.test(html), "the raw model score must not be shown as a pick");
  assert.ok(html.includes("—"));
  assert.ok(html.includes("Add odds"), "should say how to get a usable pick");
});

test("a user override is shown even when the model cannot forecast", () => {
  // The user has made a choice; withholding their own pick would be wrong.
  const app = loadApp(appData());
  const html = app.fixtureCard(fixture({
    forecast_basis: "NONE", user_pick: "3-1",
    modellability: { modellable: false, unknown_teams: ["Salford"], corpus: "E0+E1", reason: "" },
  }), 0);
  assert.ok(html.includes("3"), "the user's own pick should still appear");
  assert.ok(!html.includes("NO PICK"));
});

// ---- monitoring claim ----------------------------------------------------

test("monitoring is only claimed when a job is actually scheduled", () => {
  const idle = loadApp(appData());
  assert.ok(idle.monitorCard().includes("Monitoring not scheduled"));
  assert.ok(idle.monitorCard().includes("super6 watch"));

  const active = loadApp(appData({
    health: { scheduler_jobs_pending: 2, next_scheduled_check: "2026-08-15T10:00:00+00:00",
              warnings: [], sources: [] },
  }));
  assert.ok(active.monitorCard().includes("Monitoring active"));
  assert.ok(!active.monitorCard().includes("not scheduled"));
});

// ---- market notes --------------------------------------------------------

test("market notes describe the market, never a change to the forecast", () => {
  const app = loadApp(appData());
  const notes = app.marketNotes(fixture({
    market: {
      available: true, bookmaker_count: 6, market_divergence: "HIGH",
      model_vs_market: 0.21, last_movement: "Arsenal: 60.0% -> 70.0% (+10.0pp)",
      materiality: "MAJOR", market_agreement: "HIGH", stale: false,
    },
  }));
  const text = notes.join(" ");
  assert.ok(text.includes("already uses the newer price"),
    "must not imply movement adjusts the pick a second time");
  assert.ok(text.includes("extra caution"), "HIGH divergence should warn");
});

test("no market means no market notes at all", () => {
  const app = loadApp(appData());
  // Length, not deepStrictEqual: values cross a `vm` realm boundary, so an
  // empty array here is not reference-equal to an empty array out here.
  assert.equal(app.marketNotes(fixture({ market: { available: false } })).length, 0);
  assert.equal(app.marketNotes(fixture()).length, 0);
});

// ---- transparency --------------------------------------------------------

test("the heatmap caption states how much probability is actually drawn", () => {
  const app = loadApp(appData());
  const cells = [];
  for (let h = 0; h <= 10; h += 1) {
    for (let a = 0; a <= 10; a += 1) {
      cells.push({
        home_goals: h, away_goals: a, score: `${h}-${a}`,
        probability: h <= 6 && a <= 6 ? 0.02 : 0.001,
        expected_points: 1, selected: h === 1 && a === 0,
      });
    }
  }
  const html = app.heatmapGrid({
    heatmap: { home_team: "A", away_team: "B", max_goals: 10, cells },
  });
  assert.ok(html.includes("Showing 0–6"));
  assert.ok(html.includes("not drawn here"), "the omitted tail must be acknowledged");
  assert.ok(html.includes('class="selected"'), "the recommended cell must be marked");
});

test("transparency blocks degrade to nothing rather than throwing", () => {
  const app = loadApp(appData());
  for (const build of [app.heatmapGrid, app.evBreakdown, app.sourceFreshness]) {
    assert.equal(build(undefined), "");
    assert.equal(build({}), "");
  }
});

// ---- strategy ------------------------------------------------------------

test("the strategy panel states both sides of the trade", () => {
  const app = loadApp(appData({
    league: {
      standings: [], profiles: [], finish_first: null, finish_first_reason: "r",
      strategy: {
        mode: "DIFFERENTIATE", active: true, reason: "Deviates on X.",
        picks: ["2-0"],
        candidates: [
          { name: "DIFFERENTIATE_1", finish_first_probability: 0.237,
            expected_round_points: 7.73, description: "Deviates on X.", recommended: true },
          { name: "MAX_EV", finish_first_probability: 0.168,
            expected_round_points: 7.79, description: "Unchanged.", recommended: false },
        ],
      },
    },
  }));
  const html = app.strategyPanel();
  assert.ok(html.includes("+6.9pp"), "the gain must be quantified");
  assert.ok(html.includes("0.06"), "the cost in expected points must be quantified");
  assert.ok(html.includes("loses more often than it wins"), "it is a gamble and must say so");
  assert.ok(html.includes("Use these picks"));
});

test("an inactive strategy offers no apply button", () => {
  const app = loadApp(appData());
  const html = app.strategyPanel();
  assert.ok(!html.includes("Use these picks"));
});

// ---- small helpers -------------------------------------------------------

test("effectivePick prefers the user's own choice", () => {
  const app = loadApp(appData());
  assert.equal(app.effectivePick(fixture()), "1-0");
  assert.equal(app.effectivePick(fixture({ user_pick: "3-2" })), "3-2");
});

test("data quality explains itself rather than showing a bare label", () => {
  const app = loadApp(appData());
  const [level, copy] = app.quality([
    { availability: { data_quality: "DEGRADED", confirmed_lineups: 0 }, market: { available: true } },
  ]);
  assert.equal(level, "DEGRADED");
  assert.ok(copy.length > 20, "a quality level without an explanation is not useful");
});

test("percent and shortTeam handle the values the payload really carries", () => {
  const app = loadApp(appData());
  assert.equal(app.percent(0.6588), "65.9%");
  assert.equal(app.percent(0), "0.0%");
  assert.equal(app.shortTeam("Manchester City"), "MC");
  assert.equal(app.shortTeam("Arsenal"), "A");
});


// ---- accessibility -------------------------------------------------------

/** A .stat-table-card stub whose overflow the test controls. */
function scrollCard({ scrollWidth, clientWidth, heading = "Rank" }) {
  const attrs = {};
  return {
    scrollWidth, clientWidth, attrs,
    setAttribute(name, value) { attrs[name] = value; },
    removeAttribute(name) { delete attrs[name]; },
    hasAttribute(name) { return name in attrs; },
    getAttribute(name) { return attrs[name] ?? null; },
    querySelector: () => (heading ? { textContent: heading } : null),
  };
}

test("a table that overflows becomes a labelled keyboard-reachable region", () => {
  // WCAG 2.1.1: the stat tables have a min-width, so on a narrow viewport
  // the card scrolls sideways and a pointer was the only way to scroll it.
  const app = loadApp(appData());
  const card = scrollCard({ scrollWidth: 598, clientWidth: 341 });
  app.__context.document.querySelectorAll = () => [card];

  app.markScrollableRegions();
  assert.equal(card.getAttribute("tabindex"), "0");
  assert.equal(card.getAttribute("role"), "region");
  assert.equal(card.getAttribute("aria-label"), "Rank table, scrollable");
});

test("a table that fits gets no tab stop, so focus order stays meaningful", () => {
  // Making every table focusable would trade one keyboard problem for
  // another: tab stops at desktop width that lead nowhere.
  const app = loadApp(appData());
  const card = scrollCard({ scrollWidth: 570, clientWidth: 570 });
  app.__context.document.querySelectorAll = () => [card];

  app.markScrollableRegions();
  assert.equal(card.getAttribute("tabindex"), null);
  assert.equal(card.getAttribute("role"), null);
});

test("a card that stops overflowing loses its tab stop again", () => {
  const app = loadApp(appData());
  const card = scrollCard({ scrollWidth: 598, clientWidth: 341 });
  app.__context.document.querySelectorAll = () => [card];
  app.markScrollableRegions();
  assert.equal(card.getAttribute("tabindex"), "0");

  card.clientWidth = 598; // the viewport widened
  app.markScrollableRegions();
  assert.equal(card.getAttribute("tabindex"), null);
});

test("an existing aria-label is not overwritten by the derived one", () => {
  const app = loadApp(appData());
  const card = scrollCard({ scrollWidth: 598, clientWidth: 341 });
  card.setAttribute("aria-label", "Season standings");
  app.__context.document.querySelectorAll = () => [card];

  app.markScrollableRegions();
  assert.equal(card.getAttribute("aria-label"), "Season standings");
});

test("badge initials are hidden from assistive tech, not just styled small", () => {
  // The initials duplicate the team name rendered beside them and are a
  // fallback for a crest that failed to load — decoration, not content.
  const app = loadApp(appData());
  const html = app.teamBadge("Arsenal");
  assert.match(html, /<b aria-hidden="true">/);
  assert.match(html, /aria-label="Arsenal badge"/);
});

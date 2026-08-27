/**
 * Load app.js into a sandbox so its functions can be tested directly.
 *
 * The UI is deliberately a plain script with no build chain, so there is
 * nothing to import: no modules, no exports. Rather than restructure it
 * for testability — which would cost the very simplicity that makes it
 * runnable from a checkout — it is evaluated in a `vm` context with the
 * smallest DOM stub that lets it load.
 *
 * Top-level `let`/`const` in a script create lexical bindings that are
 * NOT properties of the global object, so tests cannot reach `appData` by
 * assigning to the context. A short shim appended to the source closes
 * over those bindings and exposes exactly what the tests need.
 */

import { readFileSync } from "node:fs";
import vm from "node:vm";

const APP = new URL("../../src/bym/web_assets/app.js", import.meta.url);

// Functions under test. Listed explicitly so adding one to the UI does
// not silently widen the surface these tests claim to cover.
const EXPOSED = [
  "escapeHtml", "percent", "titleCase", "shortTeam", "money",
  "effectivePick", "isUnmodellable", "isMarketOnly", "hasNoForecast",
  "modellabilityNote", "marketOnlyNote", "unknownTeamList", "marketNotes",
  "quality", "submissionMessage", "fixtureCard", "monitorCard",
  "heatmapGrid", "evBreakdown", "sourceFreshness", "surpriseNote",
  "strategyPanel", "submissionRecord", "leagueView", "homeView",
  "teamBadge", "activityItem", "emptyState", "markScrollableRegions",
  "lmsPlanCard", "lmsRecommendationRows", "lmsLineageCard", "lineageChart",
  "lmsView",
  "healthView", "modelView", "fplView", "fplStatusPill",
];

/** An entrant's lineage as `_lms_lineage_dict` shapes it. */
export function lmsLineage(overrides = {}) {
  return {
    survival_yours: 0.2852,
    survival_plan: 0.3719,
    steps: [
      {
        gameweek: 1, status: "BOOKED", team: "Man United", p_win: 0.7165,
        plan_team: "Arsenal", plan_p_win: 0.8104, delta: -0.0939,
        result: "PENDING", survival_yours: 0.7165, survival_plan: 0.8104,
      },
      {
        gameweek: 2, status: "PLANNED", team: "Coventry", p_win: 0.62,
        plan_team: "Man United", plan_p_win: 0.7154, delta: -0.0954,
        result: null, survival_yours: 0.4442, survival_plan: 0.5798,
      },
      {
        gameweek: 3, status: "PLANNED", team: "Man City", p_win: 0.8147,
        plan_team: "Man City", plan_p_win: 0.8147, delta: 0,
        result: null, survival_yours: 0.3619, survival_plan: 0.4723,
      },
    ],
    ...overrides,
  };
}

/** A Last Man Standing horizon plan as `lms_payload` shapes it. */
export function lmsPlan(overrides = {}) {
  return {
    picks: [
      {
        gameweek: 3, weeks_ahead: 0, team: "Brentford", opponent: "Tottenham",
        is_home: true, p_win: 0.51, p_draw: 0.25, p_loss: 0.24,
        planning_p_win: 0.51, confidence: "LOW",
      },
      {
        gameweek: 4, weeks_ahead: 1, team: "Man City", opponent: "Bournemouth",
        is_home: true, p_win: 0.78, p_draw: 0.14, p_loss: 0.08,
        planning_p_win: 0.75, confidence: "HIGH",
      },
    ],
    survival_p: 0.398, greedy_survival_p: 0.311, survival_gain: 0.087,
    greedy_team: "Man City", unplannable: [],
    ...overrides,
  };
}

function elementStub() {
  const node = {
    innerHTML: "", textContent: "", hidden: false, value: "",
    dataset: {}, style: {}, elements: {},
    classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
    close() {}, showModal() {}, focus() {}, scrollIntoView() {},
    addEventListener() {}, removeEventListener() {},
    querySelector: () => elementStub(),
    querySelectorAll: () => [],
    closest: () => null,
  };
  return node;
}

export function loadApp(appData = null) {
  const source = readFileSync(APP, "utf8");
  const shim = `
    ;globalThis.__harness = {
      set(data) { appData = data; },
      get() { return appData; },
      fns: { ${EXPOSED.join(", ")} },
    };
  `;

  const context = {
    console,
    // app.js registers `window.addEventListener("hashchange", …)` at load.
    addEventListener() {},
    removeEventListener() {},
    document: {
      ...elementStub(),
      addEventListener() {},
      querySelector: () => elementStub(),
      querySelectorAll: () => [],
      createElement: () => elementStub(),
    },
    location: { hash: "#home" },
    navigator: { clipboard: { writeText: async () => {} } },
    AbortController,
    setInterval: () => 0,
    clearInterval() {},
    setTimeout: () => 0,
    clearTimeout() {},
    // Nothing under test performs I/O; a request means the test set
    // something up wrong, and should say so rather than hang.
    fetch: async () => { throw new Error("network access from a unit test"); },
  };
  context.window = context;
  context.globalThis = context;

  vm.createContext(context);
  vm.runInContext(source + shim, context, { filename: "app.js" });

  const harness = context.__harness;
  if (appData) harness.set(appData);
  return { ...harness.fns, __setAppData: harness.set, __context: context };
}

/** A minimal, valid fixture as the payload actually shapes it. */
export function fixture(overrides = {}) {
  return {
    home_team: "Arsenal", away_team: "Everton",
    pick: "1-0", model_pick: "1-0", strategy_pick: "1-0", user_pick: null,
    result_probability: 0.66, score_probability: 0.15, expected_points: 1.77,
    confidence: "HIGH", status: "SCHEDULED",
    forecast_basis: "PHASE1_MODEL_V1",
    modellability: { modellable: true, unknown_teams: [], corpus: "E0+E1", reason: "" },
    alternatives: [], market: null, availability: null, history: [],
    ...overrides,
  };
}

export function appData(overrides = {}) {
  return {
    round: {
      round_number: 1, deadline_at: "2026-08-15T14:00:00+00:00",
      fixtures: [fixture()],
      golden_goal: { minute: 4, sky_interval: "3:00-3:59", intervals: {}, rationale: "r" },
      expected_round_score: 7.8, model_version: "PHASE1_MODEL_V1",
      submission_state: "READY", submission: { drift: [] },
      meta: { generated_at: "2026-08-08T12:00:00+00:00" },
      changes_since_last_visit: [],
    },
    health: { scheduler_jobs_pending: 0, next_scheduled_check: null, warnings: [], sources: [] },
    settings: { player_name: "You", league_name: "My League", notifications: {}, budget: {} },
    league: { standings: [], profiles: [], finish_first: null, finish_first_reason: "r",
              strategy: { mode: "NORMAL", active: false, reason: "r" } },
    activity: [], alerts: { items: [] },
    ...overrides,
  };
}

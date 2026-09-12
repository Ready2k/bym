# FPL UI Scope

## Purpose

Turn the existing FPL engines into a guided, usable workflow inside Beat Your
Mates. A manager should be able to set up a current squad, compare named
transfers, plan the next 2–4 Gameweeks, and assess the private-league impact
without editing JSON files or granting access to a paid service.

The UI remains advisory and read-only with respect to the official FPL site:
it must never submit transfers, change captaincy, activate chips, or scrape an
authenticated FPL session.

## Current State

Implemented in the local FPL decision centre:

- decision-first Gameweek, deadline, budget, chip, scope, freshness and risk
  summary;
- guided, validated 15-player squad and named-shortlist setup;
- editable 2–4 Gameweek named-shortlist transfer and chip planner;
- private-league rival scenarios and named own-plan comparison;
- assisted local-browser action confirmation without credential/session data;
- append-only pre-deadline reviews and separate post-deadline rival evidence.

The engines continue to use explicit local files for reproducibility, but the
normal workflow now creates and edits those inputs through the browser rather
than requiring JSON editing.

## In Scope

### 1. FPL Decision Home

Create a clear top-level FPL decision view with:

- next Gameweek and deadline countdown;
- current bank, free transfers and available chips;
- one primary recommendation card: hold/transfer, starting XI, captain and
  vice-captain;
- explicit advice scope (`CURRENT SQUAD ONLY`, `SHORTLIST ONLY`, or `FULL
  MARKET`);
- data freshness, uncertainty and downside before any action;
- clear links to edit squad, plan horizon and add rivals.

The page must show a helpful next action when information is missing, not a
passive `AWAITING_*` status alone.

### Assisted FPL Action Mode

Provide a local-browser assistance flow for a transfer, captain or chip
action: prepare a concise action summary, require the manager to type an exact
confirmation phrase, then record completion only after the manager confirms
they made the change in FPL. The ledger stores no credentials, cookies or page
content. A conversational agent may operate the already-signed-in local
browser only after that action is confirmed again in the conversation.

### 2. Current Squad Setup

Provide a guided form for the manager-visible state:

- 15 players with name, club, position, price, fixture and starter/bench
  order;
- captain and vice-captain;
- bank, free transfers, deadline and available chips;
- optional named shortlist candidates;
- source label and observed time.

Validation must enforce the legal 2 GK / 5 DEF / 5 MID / 3 FWD squad, 11
starters, legal bench order, and distinct starting captain/vice-captain. The
form saves only local, manager-provided data and explains that it is not an
FPL account connection.

### 3. Transfer & Chip Planner (Phase 2)

Replace the JSON-only horizon input with a step-by-step planner:

- select a consecutive 2–4 Gameweek horizon;
- enter or review selling values for owned players and buy values for named
  shortlist candidates;
- enter per-Gameweek projections, fixture count (blank/single/double) and
  fixture certainty;
- add optional price-risk scenarios;
- review hold versus each feasible named transfer path;
- show free-transfer banking, hit cost, bank after each move, fixture alerts
  and budget-invalid price scenarios;
- show Bench Boost/Triple Captain comparison against the same no-chip path;
- keep Wildcard/Free Hit as watch items unless complete market coverage and
  comparable alternative squads are available.

Every result must retain the label `SHORTLIST_ONLY_HORIZON` when the candidate
universe is incomplete.

### 4. Mates-Relative Planner (Phase 3)

Provide a private-league setup and comparison flow:

- add each rival's last locked squad, starting points and observed Gameweek;
- record optional transfer/captain history as evidence;
- define multiple *likely* current-GW rival scenarios with probabilities;
- create named versions of the manager's own plan for comparison;
- enter the projections required by the selected horizon;
- display P(sole lead after horizon), P(lead or tied), expected total and
  private-league effective ownership including captaincy;
- make it visually explicit that rival scenarios are predictions, not known
  pre-deadline facts.

The interface must prevent a current-GW rival state from being entered as a
confirmed observation before the deadline.

### 5. Decision Review & History

Add a review screen that shows:

- assumptions and provenance used for the current advice;
- why the selected option wins over its named alternatives;
- uncertainty, price and fixture risks;
- a local “reviewed” marker and timestamp;
- immutable pre-deadline decision history;
- post-deadline actual rival-pick capture as evaluation evidence only.

No UI control may claim an FPL action has been performed. The manager carries
out any change manually in FPL.

## Out of Scope

- Credential storage, session-cookie capture, unattended FPL login, or
  autonomous transfer/chip/captain submission without a fresh per-action
  manager confirmation.
- A claimed full-market recommendation without an approved complete market.
- Treating rival current-GW squads as known before deadline.
- Season-long `P(finish 1st)` until a longer-horizon, validated Phase 3 input
  exists.
- Replacing or mutating `FROZEN_PHASE1_V1` in the UI.

## UX Principles

- Lead with the decision, then show reasoning and downside.
- Prefer simple forms and sensible defaults over raw JSON.
- Preserve manager control: every inferred or uncertain value is visible and
  editable.
- Separate facts, manager-entered assumptions and model outputs.
- Fail safely: missing data reduces scope and confidence; it never fabricates
  a recommendation.
- Make the no-paid-service workflow a first-class experience, not a fallback.

## Acceptance Criteria

The UI scope is complete when a manager can, without editing files:

1. enter and validate a 15-player local squad snapshot;
2. see a transparent one-GW lineup/captain view;
3. create a 2–4 GW named-shortlist transfer plan and understand each budget,
   hit, fixture and price-risk consequence;
4. add rival scenario assumptions and compare their own named options on a
   mates-relative basis;
5. review the exact pre-deadline assumptions and recommendation later;
6. understand at every point what is local/manual, uncertain, or unavailable.

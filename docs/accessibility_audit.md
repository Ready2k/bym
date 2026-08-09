# WCAG Accessibility Audit

Date: 2026-08-09
Target: the local decision UI (`super6 serve`), all 12 pages
Tooling: axe-core 4.10.2 run in-page against the live server, at desktop
(1280px) and mobile (375px) widths, plus a manual static review
Standard: WCAG 2.1 A/AA, as axe tags them (`wcag2a`, `wcag2aa`, `wcag21aa`)

This is the first formal pass. Before it, the UI had `aria-live`,
`aria-label` and skip-link markup added incidentally as pages were built,
never audited — so the interesting question was whether ad hoc care had
actually produced an accessible result.

Largely it had. Two issues were found across twelve pages, one a real
WCAG level-A failure.

## Method

axe-core was served from the app's own `/assets/` route so it ran
same-origin against the real rendered DOM, rather than against a
reconstruction. Every page was visited by hash route, allowed to render,
then scanned. The sweep was repeated at 375px because the stat tables
carry a `min-width` and only overflow on a narrow viewport — the desktop
run alone would have missed the one serious finding.

## Findings

### 1. Scrollable tables were not keyboard reachable — FIXED

**WCAG 2.1.1 Keyboard (level A)**, axe rule
`scrollable-region-focusable`, impact *serious*. 4 nodes across 3 pages
(League, Performance, System Health).

`.stat-table-card` is `overflow-x: auto` wrapping a table with
`min-width: 570px`. Below about 600px the card scrolls sideways, and a
mouse or trackpad was the only way to scroll it: the container had no tab
stop, so a keyboard-only user could not reach the columns off the right
edge. On the League page that hides the points and exact-score columns —
the actual content of the page.

Fixed in `markScrollableRegions`, called after every render and on
resize. Cards that genuinely overflow get `tabindex="0"`, `role="region"`
and a label derived from their first heading ("Rank table, scrollable").

Deliberately conditional. Giving every table a permanent tab stop would
have silenced the rule while adding stops that lead nowhere at desktop
width — trading a keyboard problem for a different keyboard problem. The
rule only applies to regions that actually scroll, and so does the fix.

### 2. Badge initials were undecidable for contrast — REVIEWED, then FIXED

axe rule `color-contrast`, reported *incomplete* (not a violation) on 12
nodes per page: the one- and two-letter club initials inside
`.team-badge`, at 7.68px. axe could not decide, for two reasons it
reported separately — `shortTextContent` (too few characters to be sure
it is text at all) and `bgOverlap` (the crest image sits over it).

Resolving an incomplete is the auditor's job, so it was measured rather
than waved through:

```text
text        rgb(52, 64, 84)
background  rgb(242, 244, 247)
ratio       9.49:1        (AA large/normal both require ≤ 4.5:1)
```

**Contrast passes comfortably.** Worth recording that the crests were not
loading during the audit (`crests.football-data.org` unreachable from the
preview), so the initials were what a sighted user actually saw — which
makes 9.49:1 the figure that matters, not a hypothetical.

Separately, the initials are marked `aria-hidden="true"`. They duplicate
the team name rendered as real text beside them and exist only as a
fallback for a crest that failed to load. `role="img"` on the parent
already made descendants presentational; the explicit attribute states
the intent and keeps a stray "A" out of a screen reader's output.

axe still reports these as incomplete afterwards — it does not exclude
`aria-hidden` nodes from that bucket. That is expected and correct: an
incomplete means "a human must decide", and this document is that
decision.

## What passed without changes

- **Skip link** is the first focusable element and targets `#main`, which
  carries `tabindex="-1"` to receive focus.
- **Landmarks and labelled navigation**: `<aside aria-label="Primary
  navigation">`, two `<nav>` elements with distinct labels, one `<main>`.
- **Live regions** already correct: `role="status"` on the loading panel
  and toast, `role="alert"` on the error panel, `aria-live="polite"`.
- **No positive `tabindex`** anywhere — natural focus order is intact.
- **No fake buttons**: every `[data-action]` element is a real `button`,
  `a` or `input`, so keyboard activation comes for free.
- **Every form control has an accessible name** (0 unlabelled inputs,
  selects or textareas across all pages).
- **Dialogs** use `showModal()`, giving a native focus trap; focus was
  confirmed to move inside on open and the dialog to match `:modal`. Each
  also has a visible close button with an explicit `aria-label`.
- **38 axe checks pass** on the Home page alone.

## Not verified, and why

**Keyboard interaction could not be exercised through automation.** The
browser harness used for this audit swallows synthetic key events before
they reach the page — a listener registered in the page recorded
`keydown: 0` for both Escape and Tab, so the absence of a reaction proves
nothing about the app. Escape-to-close and tab-order were therefore
assessed from the mechanism rather than observed: `showModal()` provides
both natively, nothing in `app.js` registers a `cancel` handler that
could suppress Escape, and there are no positive tabindex values to
disturb order. A real keyboard, driven by hand, is still the honest way
to confirm this and has not been done.

**No screen-reader pass.** Nothing here has been listened to with
VoiceOver, NVDA or JAWS. Static structure is sound and axe's ARIA rules
pass, but that is not the same as the experience being coherent —
particularly the strategy panel, where the meaning depends on comparing
two probabilities that are visually adjacent and aurally far apart.

**Zoom and reflow (WCAG 1.4.10) untested** beyond the 375px sweep, and
**prefers-reduced-motion** is not honoured anywhere, though the UI has
very little motion to reduce.

## Regression cover

`tests/js/app.test.mjs` gains five tests: an overflowing card becomes a
labelled focusable region, a fitting card does not, a card that stops
overflowing loses the tab stop again, an author-supplied `aria-label`
survives, and badge initials stay `aria-hidden`. These pin the two
decisions above so a later refactor cannot quietly undo them.

The axe sweep itself is **not** automated. It needs a live server and a
real browser, and wiring that into pytest would add a heavyweight
dependency to a suite that currently runs in 90 seconds from a checkout.
Re-run it by hand when the UI changes materially; the method is described
under "Method" above and takes a few minutes.

# Design: docmatch
Inherits ~/.claude/design/DESIGN.md. Only slots and overrides live here.

The one surface is the review page under `apps/review` (#156, #174): a reviewer reads a scanned invoice beside a three-way ledger and approves or rejects it.

## Slots
- Accent hue: oklch(0.50 0.13 255), --primary-text oklch(0.47 0.13 255) on the light page and oklch(0.78 0.10 255) on the dark one, both at 4.5:1 or above. Reason: the ledger is full of hold red, note amber and agreement green, so the accent has to be the one hue none of the three can be mistaken for; a ledger-ink blue does that, stays apart from the owner's other products (165, 47, 195), and keeps the hue the owner already walked in the #156 prototype.
- Type pairing: IBM Plex Sans + JetBrains Mono. Reason: the page is an operate surface of figures and codes, the fallback pairing reads like a datasheet, and the prototype the owner chose was set in it.
- Neutral temperature: achromatic. Reason: the scanned page is the only image on screen and a tinted housing would shift how its paper reads.
- Signature motion: the decide bar's outcome slides up into place when a decision commits, placements: the decide bar's outcome, the confirmation's list of what is open. Reason: deciding is the page's core action and the one moment the instrument answers the reviewer.

## Overrides
- App shell (icon-rail sidebar plus 64px header): replaced by one top bar holding the queue strip. Reason: rule 1 allows one page, and #156 put the queue as a strip on it, so there is nothing for a sidebar to navigate.
- shadcn components: not used; plain elements styled from the tokens. Reason: the page needs a button, a details disclosure and a table, none of which needs a primitive, and rule 7 prefers fewer components.
- Confirm dialogs: the approval confirmation is inline in the decide bar, never a dialog. Reason: #156 chose it, and it keeps what is open in view beside the ledger that shows it.
- Confidence: a gray number in the data face, never colored, never weighted, never a bar. Reason: Azure's header confidence is uncalibrated (#48), so it must not look like a verdict (#156).
- Decide bar: bottom-anchored on every width, sticky at the foot of the ledger column, where the personal file anchors desktop actions at the top. Reason: #156 chose prototype D, which puts the decision after the ledger the reviewer has just read, and the bar keeps it in view while the ledger scrolls.
- Line numbers: the ledger numbers lines from 0. Reason: the engine's finding explanations shown beside them count from 0 (#76), and two counts on one row would contradict each other.

## Benchmarks
- The #156 prototype D (branch `prototype/156-review-page` at `b48e239`): the layout, the ledger per purchase-order line, the page pinned beside it.

# Footage verification from as-built sequentials — plan

Status: **proposal** · Supersedes the cross-check removed in `81cdf08`
(recoverable at `095a57c`)

## 1. What we are trying to do

A construction supervisor checks a contractor's as-built by hand: for each span,
read the fiber sequentials at either end, subtract, and see whether the difference
matches the footage written on the comment. On a 400-span book that is a day's
work, and it is the last line of defence before a footage is billed.

The drawings already carry everything needed to do it. The first attempt proved
the arithmetic holds — where the chain was intact it reconciled exactly, and it
found 16 genuine mis-keyed footages across four books. It was withdrawn because
the surrounding machinery produced too many findings that were not errors.

## 2. Why the first attempt fell short

Worth being precise, because each failure points at a fix.

**It inferred the route instead of reading it.** Spans were ordered by station
number and neighbours assumed to be adjacent. Real routes leave a sheet and come
back, so "adjacent in station order" often meant "hundreds of feet and several
sheets apart". Every workaround for this — page proximity, run-length limits,
filler nodes — was a proxy for information the drawing states plainly.

**Each guess compounded the last.** Route membership was inferred, then the
stationing direction, then whether a coil sat inside or outside the footage, then
whether a run was a break. Four inferences deep, a wrong answer at any level
produced a confident-looking finding with nothing behind it.

**There was no ground truth.** Accuracy was measured as "percentage that
reconciles", which is not accuracy at all — it counts agreement with our own
inference. We never knew whether a flagged span was genuinely wrong, so we could
not tell tuning from overfitting.

**It answered a question nobody asked.** The output was a list of spans that
failed an internal test. The supervisor's question is "which footages should I not
pay", and a list where half the entries are drafting artefacts does not answer it.

**Buried work was pushed too early.** It has no intermediate sequentials, so its
chain can never be dense. It needed a different treatment and got the aerial one.

## 3. What changes: read the sheet, don't infer it

Every comment carries its position on the sheet, and we have never used it.
Sorting page 5 of PON 10 left to right:

| x | station | footage | gap to the next |
|---|---|---|---|
| 90 | 24,470 | 212 ft | **212** |
| 380 | 24,258 | 272 ft | **272** |
| 772 | 23,986 | 280 ft | **280** |

The route order falls straight out of the geometry, and each gap matches the
footage claimed at its own end. No station arithmetic, no direction detection, no
break heuristic — the sheet says which span follows which.

This is the foundation. Position gives the sequence; the sequentials are then only
used for the one thing they are for, which is checking the number.

## 4. Plan

### Phase 0 — Ground truth (no code)

Nothing else is worth building until we can tell a real finding from a false one.

Take one route from one book — PON 10's 48ct feeder is the cleanest — and have the
supervisor mark, span by span: correct, wrong (with the right value), or a genuine
break in the route. Thirty to fifty spans is enough.

*Deliverable*: a small CSV checked into `samples/`.
*Exit*: every later phase reports precision and recall against it, not a
self-referential "reconciles" percentage.

### Phase 1 — Route order from the sheet

Extract each comment's position, already available and unused. Within a sheet,
order comments by position along the route's axis; across sheets, follow sheet
order. Detect the axis per sheet rather than assuming (some run left to right,
some right to left — PON 10 descends as x increases).

*Deliverable*: an ordered sequence of comments per route, derived from geometry.
*Exit*: on the Phase 0 route, the derived order matches the supervisor's own
reading of the route with no arithmetic involved.

### Phase 2 — Chains the coordinator confirms, and the tool remembers

Show the derived chain and let a human split or join it where the drawing is
ambiguous. Persist the confirmed topology per project, exactly as confirmed
crosswalk mappings already persist — the same book reviewed next month starts from
the answer, and a second book from the same contractor starts from a better guess.

*Deliverable*: a `route_chain` table and a confirm/split interaction.
*Exit*: a chain confirmed once is not re-derived, and the confirmations survive a
reload.

### Phase 3 — The check, narrowed

Only check spans whose neighbours are confirmed adjacent. For those, the
arithmetic is what it always was, and the coil handling that already works stays.

Everything else is reported as **not checked**, plainly. A supervisor who knows
sixty spans were checked and forty were not is better served than one handed a
hundred results of unstated quality.

*Deliverable*: a check that runs only on confirmed chains.
*Exit*: precision on the Phase 0 route above 90% — of what it flags, nine in ten
are real. Recall matters less at first; a check that misses half the errors but
never cries wolf still saves the supervisor most of the work, and gets trusted.

### Phase 4 — Show the route, not a table

The old panel listed failures out of context. The supervisor works along a route,
so the tool should too: a strip of the route, station by station, each run drawn
in proportion and coloured by whether its footage closes.

Reading it: a green run reconciles; a red one is off, and by how much; a grey one
was not checked. Clicking a run shows the two comments that bound it, so the
number can be settled without leaving the page. Where the chain looks broken the
strip breaks visibly, and the coordinator can confirm or correct it right there.

Findings sort by evidence: footages the drawing contradicts by a small margin
first, because those are almost always typing errors.

*Deliverable*: the route strip in the As-built step.
*Exit*: a supervisor can go from opening the book to a decision on a flagged span
without opening the PDF.

### Phase 5 — Buried work

Only after the aerial path is trusted. Buried runs have entry and exit
sequentials, and the conduit between two runs equals the distance from one exit to
the next entry — verified exactly on PON 9's 288ct chain. What they lack is
anything in between, so this phase leans entirely on Phase 1's ordering rather
than on chain density.

*Exit*: same precision bar, measured on a buried ground-truth set of its own.

## 5. What this deliberately does not do

- **No fully automatic verdict on every span.** The tool reports what it can stand
  behind and says so about the rest.
- **No tolerance.** A footage either closes or it doesn't; this stays exact.
- **No inferring conventions four levels deep.** Where the drawing is ambiguous the
  coordinator settles it once and the answer is remembered.

## 6. Risks

**The geometry may not order every book.** It works on PON 10; PON 9 and 11 are
unproven. Phase 1 exits on evidence, and if a book won't order geometrically it
falls back to being unchecked rather than guessed at.

**Ground truth is manual.** It is a genuine ask of the supervisor's time. It is
also the only thing that separates this attempt from the last one.

**Confirmed chains are a new thing to maintain.** Mitigated by persisting them the
same way as crosswalk aliases, which already work this way and are understood.

## 7. Order of work

Phase 0 gates everything. Phases 1–3 are one continuous piece of work and should
land together behind the ground-truth measurement. Phase 4 can follow immediately;
Phase 5 only once aerial is trusted in real use.

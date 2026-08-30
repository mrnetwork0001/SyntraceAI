# SyntraceAI - Design System (FROZEN)

The visual language for `dashboard/landing.html` and `dashboard/index.html`. Both pages
MUST use these exact tokens and section patterns so they read as one product.

Reference feel: an editorial, high-end AI-infrastructure site - **light and airy by
default, punctuated by full-bleed dark sections and soft pastel-gradient bands**. Large
light-weight headings, very generous whitespace, big rounded cards, restrained colour
used only where it carries meaning.

This replaces the previous dark neon theme. **All copy and all functionality are
preserved** - only structure and styling change.

---

## 1. Tokens

```css
:root {
  /* surfaces, light to dark */
  --paper:      #ffffff;   /* white sections */
  --paper-2:    #f7f7f5;   /* off-white / default page ground */
  --paper-3:    #efefec;   /* subtle grey band */
  --ink:        #0c0c0d;   /* dark sections + primary text */
  --ink-2:      #17181b;   /* dark card surface */
  --ink-3:      #232529;   /* dark card border / hairline */

  /* text */
  --text:       #121316;   /* on light */
  --text-muted: #63666e;   /* on light, secondary */
  --text-faint: #8b8e96;   /* on light, tertiary / labels */
  --on-dark:        #f4f4f2;
  --on-dark-muted:  #a2a5ad;
  --on-dark-faint:  #71747c;

  /* hairlines */
  --line:       #e3e3df;   /* on light */
  --line-dark:  rgba(255,255,255,.12);

  /* semantic data colours (meaning only - never decoration) */
  --good:  #1c8a52;   --good-soft:  #e4f4ea;
  --bad:   #c8453c;   --bad-soft:   #fbe9e7;
  --info:  #3b5bdb;   --info-soft:  #e8ecfd;
  --warn:  #a8730a;   --warn-soft:  #fdf3e0;

  /* pastel gradient stops (soft section bands + blobs) */
  --g-mint:   #c9e9d2;
  --g-sky:    #cfe2f5;
  --g-lilac:  #dcd4f2;
  --g-blush:  #f7d4dd;
  --g-peach:  #fbdcc2;
  --g-cream:  #f6efd8;

  --radius-lg: 28px;   /* section cards, hero panels */
  --radius:    18px;   /* standard cards */
  --radius-sm: 12px;   /* inputs, small tiles */
  --radius-pill: 999px;

  --sans: "Inter Tight", Inter, system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace;

  --maxw: 1180px;                       /* content column */
  --gutter: clamp(20px, 4vw, 48px);
}
```

Fonts: load Inter Tight (400,500,600) + JetBrains Mono (400,500) from Google Fonts with
real fallbacks. Body defaults to `--paper-2`, `--text`, 16px/1.65.

**Side margins:** keep the existing 50%-margin container rule -
`.wrap { max-width: calc(50vw + 590px); margin: 0 auto; padding: 0 var(--gutter); }`.

## 2. Rhythm & section pattern

Sections alternate surface to create the editorial rhythm. Each section is full-bleed;
content sits in `.wrap`.

| Order | Section | Surface |
| :-- | :-- | :-- |
| 1 | Hero | `--ink` (dark), full-bleed |
| 2 | The loop (numbered stepper) | `--paper-2` |
| 3 | Engine pipeline | `--ink` (dark) |
| 4 | Stat row | `--paper` |
| 5 | Trust strip | `--paper` (hairline top) |
| 6 | Results / tabs | pastel gradient band |
| 7 | Honest by design | `--paper` + gradient blob |
| 8 | Findings (real generated tests) | `--g-cream` tint |
| 9 | Closing CTA | dark card on `--paper-2` |
| 10 | Footer | `--ink` + giant wordmark |

Section padding: `clamp(72px, 9vw, 128px)` top/bottom. Never a hard colour edge without
either a hairline or a full-surface change.

## 3. Type scale

```
display   clamp(40px, 6.2vw, 74px)  weight 500  line-height 1.03  letter-spacing -.035em
h2        clamp(30px, 4vw, 46px)    weight 500  line-height 1.08  letter-spacing -.03em
h3        20px                      weight 500  letter-spacing -.01em
lede      clamp(17px, 1.5vw, 20px)  weight 400  colour --text-muted  max-width 60ch
body      16px/1.65
label     11px  mono  uppercase  letter-spacing .16em  colour --text-faint
data      mono, tabular-nums
```

Headings are **light-weight and large** - never bold-heavy. Two-line headlines with a
muted second clause are the house style (e.g. "Results in minutes." / supporting line).

## 4. Components

- **Card** - `background: var(--paper)`, `border: 1px solid var(--line)`,
  `border-radius: var(--radius)`, padding `28px`. No drop shadows on light; use hairlines.
  On dark: `--ink-2` + `--line-dark`.
- **Button** - pill, `padding: 12px 22px`, 15px/500.
  Primary: `--ink` bg, `--on-dark` text (inverts to white bg + ink text on dark sections).
  Secondary: transparent, 1px `--line` border.
- **Pill / badge** - `--radius-pill`, 11px mono uppercase, tinted soft background
  (`--good-soft`, `--info-soft`, …) with the matching solid colour as text.
- **Numbered stepper** (loop section) - left rail of items `1. Inject`, `2. Isolate`,
  `3. Score`, `4. Heal`; the active one gets an `--ink` left border and full text, the
  rest are collapsed to their title in `--text-faint`. A large panel on the right shows
  that step's detail. Auto-advances every 4s and on click; pauses on hover.
- **Stat row** - 3-4 cells separated by vertical hairlines. Number:
  `clamp(38px, 5vw, 60px)`, weight 500, tabular-nums, with the unit/symbol in the
  semantic colour. Caption below in `--text-muted`, 14px.
- **Tabs** - pill row, active = `--ink` fill + white text, inactive = transparent with
  `--line` border. Panels swap without layout jump.
- **Gradient band** - `linear-gradient(120deg, var(--g-mint), var(--g-sky) 30%,
  var(--g-lilac) 55%, var(--g-blush) 78%, var(--g-peach))` at low opacity over `--paper`,
  plus 1-2 blurred radial blobs. Text stays `--text` (never white on pastel).
- **Gradient blob** - a 380px rounded shape with the pastel gradient, `filter: blur(38px)`
  at `opacity: .55`, used once in "Honest by design".
- **Giant wordmark** - footer bottom: `SYNTRACEAI` at `clamp(56px, 15vw, 190px)`,
  weight 600, letter-spacing -.05em, colour `rgba(255,255,255,.09)`, clipped so it bleeds
  off the bottom edge. Decorative: `aria-hidden="true"`.

## 5. Motion

Restrained and purposeful. Keep every existing animation but retune for a light theme:

- Section content fades up 10px on scroll into view (IntersectionObserver, once, 500ms
  cubic-bezier(.16,1,.3,1), stagger 60ms). Never animate on every scroll.
- The **engine pipeline keeps its SVG wires and travelling particles** (it lives on a
  dark section, so the existing violet/cyan/red/green particle colours still work).
  Re-tune wire stroke to `rgba(255,255,255,.22)`.
- Tickers, emitter bars, the sandbox ring and the probe bars are preserved.
- Everything respects `@media (prefers-reduced-motion: reduce)`.

## 6. Content mapping (copy is preserved - do not invent claims)

Every number stays live-loaded from `/api/reports`; placeholders show `-` until a report
exists. **Never hard-code a metric that the API can supply.**

| Reference pattern | SyntraceAI content |
| :-- | :-- |
| Dark hero + media collage | Existing hero: eyebrow badge, "We break your code faster than your tests notice.", lede, Launch App / Reproduce it, the live campaign terminal panel (kept, restyled for dark) |
| "Results in minutes" stepper | The loop: 01 Inject / 02 Isolate / 03 Score / 04 Heal (existing copy) |
| Dark model cards + diagrams | The animated engine pipeline (kept whole) + the three quality cards (Deterministic / Honest / Validated) |
| Big stat row | 50 bugs per campaign · live final mutation score · live healed-test count · $0 API cost |
| Customer logo strip | **Honest substitute:** a "runs on" strip - Python 3.11+, pytest, coverage.py, pydantic, Apache 2.0. No fabricated customer logos. |
| Tabbed workflow section | Tabs over the real report sets: **Demo app / humanize 4.16.0 / Your project** - each panel shows that target's live numbers; the "Your project" tab explains the one-file adapter and links to the dashboard |
| "Secure by design" | "Honest by design": offline, no account, no upload; equivalent mutants reported not hidden; byte-stable deterministic reports |
| Customer story quotes | **Honest substitute:** "What it found" - real auto-healed assertions from the humanize run (the `intword`/`naturalsize`/`metric` asserts), presented as evidence cards, not testimonials |
| Full-bleed CTA card | "Coverage lies. Mutation score doesn't." + Launch App |
| Big footer + wordmark | Existing footer columns + giant SYNTRACEAI wordmark |

**Prohibited:** fabricated customer names, logos, testimonials, or any metric not read
from a real report.

## 7. Dashboard (`index.html`) specifics

Same tokens; an application shell rather than a marketing page.

- **Top bar** - white, hairline bottom, sticky: wordmark + "Mission Control", report-set
  select, status pill, `?`, "Landing".
- **Run panel** - white card: `Run against` input + preset pills + primary
  "Run Mutation Campaign" + secondary buttons + the hint line. Unchanged behaviour.
- **Onboarding card** - the "How this works" 01-04 grid, now on a pastel gradient band,
  dismissible, restored by `?`.
- **Scoreboard** - the five metrics as a stat row (hairline-separated, not boxed tiles),
  keeping the plain-language labels and `title=` hover help.
- **Gap callout** - `--warn-soft` background, `--warn` left rule.
- **Tables** (survivors, healed tests) - white cards, hairline rows, mono for ids and
  locations, soft-tinted status pills. Horizontal scroll inside the card only.
- **Operator bars** - `--good` / `--bad` segments on `--paper-3` track.
- **Log panel** - the one deliberately dark element: `--ink` card, mono, `--on-dark`.

## 8. Hard requirements

1. **Preserve every element `id` and the entire `<script>` block** of both pages. The
   markup around them may change freely; the JS contract may not.
2. Preserve all existing copy. Rewrite only where this document explicitly says so.
3. Self-contained: inline CSS/JS, no build step, no external assets except the two Google
   Fonts stylesheets. No JS libraries.
4. Responsive: single column below 900px; the pipeline stacks and hides its wires; no
   horizontal page scroll at any width (grid children need `min-width: 0`).
5. Accessible: visible focus rings, `aria-hidden` on decorative art, tab controls are
   real `<button>`s with `aria-selected`, contrast >= 4.5:1 for body text.
6. `prefers-reduced-motion` disables transforms, particle motion and auto-advance.

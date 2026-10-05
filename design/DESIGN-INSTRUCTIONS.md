# Design instructions (for Claude Design)

These designs will be built exactly as drawn. Treat everything you produce as the spec that developers copy, not as inspiration.

## 1. Use the real design system
- If `/design-sync` has been run for this repo, use only its components, colours, fonts and spacing.
- If not, define a small token set first (colours, type scale, spacing, radius, shadows) and use only those tokens everywhere. No one-off values.
- Web: React + Tailwind + shadcn/ui components. iOS: SwiftUI-friendly layouts (system fonts or the named custom font, standard spacing multiples of 4).

## 2. What to deliver for every screen
- Every screen and every state: default, loading, empty, error, success, disabled, long text, and dark mode if supported.
- Fixed viewports: mobile 390×844, tablet 834×1194, desktop 1440×900 (iOS: iPhone 390×844 only unless stated).
- Realistic sample data in a separate `sample-data` file, not hard-coded inside components.
- Interactions spelled out: what each button, link and gesture does, and any animation (duration and easing).

## 3. Build it so it can be copied, not redrawn
- Split into small named components (e.g. `Header`, `AgentCard`, `MessageBubble`) that map one-to-one to real code components.
- Use real layout (flex/grid), not absolute positioning or images of UI.
- No placeholder images of text, no screenshots inside the design, no lorem ipsum in final screens.
- Icons from one named set (e.g. Lucide or SF Symbols), with names noted.

## 4. Include a MANIFEST.md in the export
- Feature name, date, version.
- List of screens and states, with file names.
- Token table (name → value).
- Component list (name → purpose → which screens use it).
- Open questions or anything deliberately left undecided.
- Anything that must NOT change during development.

## 5. Handoff
- When the design is approved, use **Handoff to Claude Code**.
- Claude Code commits the export unchanged to `design/<feature>/<YYYY-MM-DD>/` in the repo, generates reference screenshots into `design/<feature>/refs/`, and opens a PR. That PR is the design lock.
- Any later change is a new dated folder, never an edit to an old one.

## 6. Rule for developers (copied into the repo's CLAUDE.md / AGENTS.md)
> The committed design export is the spec. Copy its components and tokens directly; only wire up data, navigation and logic. Any deviation needs a note on the ticket. A PR fails if its screenshots differ from `design/<feature>/refs/` beyond the agreed threshold (web: maxDiffPixelRatio 0.02; iOS: reviewed side-by-side, then snapshot-locked).

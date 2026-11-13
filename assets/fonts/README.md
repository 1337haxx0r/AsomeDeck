# Bundled fonts

The app asks for **Roboto** (UI) and **Roboto Mono** (data, paths, labels). Neither ships
with Windows, so both have to travel with the app — spec §14.4.

The design system originally named **Barlow** for the UI. It was changed: Barlow is a
condensed grotesque, and at 13px it sets the same string in 203px where Roboto needs 264 —
a 30% squeeze that read as cramped. See the note in `ui/theme.py`.

Drop the `.ttf` files here. `asomedeck.ui.theme.load_fonts()` registers every `.ttf` and
`.otf` in this directory at startup, before any stylesheet is applied.

Nothing here is required to run. If a family is missing, `load_fonts()` logs a warning and
installs a substitution — Segoe UI for Roboto, Consolas for Roboto Mono. The design loses
its typography but the app starts, which is the right trade for a cosmetic dependency.

Worth knowing on Windows 11: Roboto is usually already installed, Roboto Mono usually is
not. So the UI gets its intended face and the *terminal* silently runs Consolas. If you
would rather not vendor anything, `Cascadia Mono` ships with Windows and has a taller
x-height than Consolas (0.517 vs 0.489 em) — change `FONT_MONO_FALLBACK` in `ui/theme.py`.

## What to fetch

| Family | Weights used | Licence |
|---|---|---|
| Roboto | 400, 500, 600 | SIL Open Font License 1.1 |
| Roboto Mono | 400, 500, 600 | SIL Open Font License 1.1 |

Both are on Google Fonts. Static instances are preferred over variable fonts —
`QFontDatabase` handles named weights more predictably than variable axes.

Expected filenames are irrelevant; the loader globs the directory.

## Why these are not committed

Around 1–2 MB of binary assets in git, for files that are trivially re-fetchable and
whose licence requires shipping the OFL text alongside them. Left as a deliberate choice
rather than an oversight — if you would rather vendor them, drop them in and commit; the
loader needs no change.

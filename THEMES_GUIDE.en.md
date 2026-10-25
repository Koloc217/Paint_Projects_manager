# How to create a UI theme

[Русский](THEMES_GUIDE.md) · **English**

A short guide for a person, or for an AI model, that makes a theme. A theme changes only the look (colors, fonts,
borders, background, charts); the markup, texts and app logic stay untouched.

## 1. How it works

- A theme is a class on `<body>` shaped `theme-<name>` (for example `theme-neon`). The standard look has no class.
- All markup is built from Tailwind classes (`bg-slate-800`, `text-slate-400`, `bg-indigo-600`...), so a theme overrides
  **those same classes** with selectors like `body.theme-<name> .bg-slate-800 { ... !important }`.
  `!important` is required: without it Tailwind rules of the same selector strength may override the theme.
- Charts (Chart.js) are drawn on a canvas and do not see CSS. So their colors and font come from the **CSS variables
  `--chart-*`** that the theme sets in its block (section 4). The app redraws the charts when the theme changes.
- The chosen theme is remembered in the browser (`localStorage`, key `ph_skin`). The login page applies the theme class too.

## 2. What to do (3 steps)

1. **A CSS block** at the end of `static/themes.css`, always between markers (the check relies on them):

   ```css
   /* === THEME: theme-neon === */
   body.theme-neon { ... }
   body.theme-neon nav { ... }
   /* === END THEME: theme-neon === */
   ```

2. **Registration**, one line in the `THEMES` registry in `static/app.js`:

   ```js
   { id: 'theme-neon', label: () => '🌈 Neon' },
   ```

3. **Fonts** (if you need non-system ones): fonts are stored in the app, no internet needed. Add the family with its
   weights to `FONTS` in `tools/build_assets.py` (a [Fontsource](https://fontsource.org) package name, for example
   `"russo-one": (400,)`), run `python tools/build_assets.py` (needs Node.js) and commit `static/fonts/`.
   The font **must include Cyrillic**: the default interface language is Russian.
   Images (background, textures) go into `static/backgrounds/` and are referenced with a relative path
   `url('backgrounds/name.jpg')` (relative to `themes.css`). External image links are not used.

Check: `python tools/check_theme.py theme-neon`, then visually (section 7).

## 3. Rules (the contract)

1. Every selector in the block starts with `body.theme-<name>`. Nothing "global" (`select {}`, `h1 {}`), otherwise the
   theme leaks into the others.
2. Change the **look**, not the layout: colors, background, borders, shadows, corner radius, fonts, decoration through
   `::before/::after`. Do not change sizes, margins, `display` or `position` of the elements themselves (exception:
   decoration in pseudo-elements). Do not hide elements (`display: none`), except the calendar icon `.dl-icon`.
3. Every property gets `!important`, except `--variables`, `transition`, `animation`, `content`.
4. **All** chart tokens (section 4) are set in the `body.theme-<name> { ... }` rule.
5. `body` has `background-color` (or `background`) and `color`. Text contrast against the background is at least 4.5:1
   (`check_theme.py` can compute it for `#hex` colors).
6. Animation names start with `<theme name>-`: `@keyframes theme-neon-glow`. No custom `@font-face` or `@import`.
7. Decorative pseudo-elements: `pointer-events: none`, `z-index` no higher than 40 (app modals start at 60, the
   confirmation dialog is 95, toasts 100).
8. Dates in cards include the year (`21.03.2027`, about 10 characters); deadline plates must fit that.
9. No heavy effects: no `filter: blur()` on large blocks and no endless animations on dozens of elements.
10. If the font has old-style figures (the zero in `05` looks like an `o`), add
    `font-variant-numeric: lining-nums` for `body`, `select`, `input`, `button`.
11. Phone (screens narrower than 640 px, `static/mobile.css`): the `#navTabs` block becomes a bottom bar and the header
    wraps into two rows. For that, `nav` gets `transform`, `filter`, `backdrop-filter`, `clip-path`, `mask` reset, and
    `nav::before` / `nav::after` are hidden. Header decorations made for a single row are not visible on phones. The
    theme colors the tab bar as usual (`nav .bg-slate-900`, buttons `nav button[id^="tab-btn"]`). Check the theme at a
    width of 375 px too.

## 4. Chart tokens

Set in the `body.theme-<name> { ... }` rule. The defaults live in `:root` at the top of `themes.css`.

| Token | What it colors | Example |
|---|---|---|
| `--chart-font` | font of the chart labels (must be available to the page) | `'Alegreya', Georgia, serif` |
| `--chart-text` | X axis labels and the legend | `#e6d5b8` |
| `--chart-muted` | Y axis labels | `#b9a47a` |
| `--chart-grid` | grid lines | `rgba(133, 103, 53, 0.35)` |
| `--chart-stage-1` … `--chart-stage-7` | "Stage funnel" bars: no stage, printing, sanding, assembly, priming, painting, finishing | `#5a4627` … |
| `--chart-series-1` | "Tasks closed" | `#c9a227` |
| `--chart-series-2` | "Projects completed" | `#8b1a1a` |
| `--chart-border` | bar outline (`transparent` for none) | `#f3e5ab` |
| `--chart-border-width` | outline width, a number in px | `1` |
| `--chart-radius` | bar corner radius, a number in px | `0` |

Give chart colors as `#hex`, `rgb()` or `rgba()`. The stage bars must differ from each other, so use six distinct colors,
not shades of one.

## 5. What to hook into

Tailwind colors carry meaning. Page: `slate-900`, cards and modals: `slate-800`, inputs and nested panels: `slate-900`,
secondary buttons and borders: `slate-700`, regular text: `slate-300/400`.

| What | Selector |
|---|---|
| Page | `body.theme-x` |
| Header | `body.theme-x nav`, tabs `nav button[id^="tab-btn"]` (the active one has the `bg-slate-700` class), the counter `#tokenCounter` |
| Headings | `h1, h2, h3` |
| Cards, modals, panels | `.bg-slate-800`, `.bg-slate-800\/90` |
| Inputs and nested panels | `.bg-slate-900`, `input`, `select`, `textarea`, `select option` |
| Project card | `#projectsContainer > div` |
| "Upcoming tasks" row | `#tasksContainer > div` |
| Other lists | `#allTasksContainer`, `#hallOfFameContainer` |
| "+ Project" button | `button.bg-emerald-600` |
| "+ Task" button, main action | `button.bg-indigo-600` |
| "Save" button | `button.bg-blue-600` |
| "Delete" button | `button.bg-red-600` |
| Secondary buttons (gear, "Cancel") | `button.bg-slate-700` |
| Deadlines | `.text-xs > span` inside cards; states: `.text-red-500` overdue, `.text-red-400` due soon, `.text-slate-500` closed or no date, `.text-slate-400` normal; calendar icon `.dl-icon` |
| Modals | `#settingsModal`, `#projectModal`, `#editProjectModal`, `#editTaskModal`, `#projectDetailsModal`, `#taskModal`, `#templateModal`, `#trashModal`, `#confirmModal`, `#lightbox`; toasts `#toastContainer` |
| Settings tabs | `#settingsModal button[id^="cfgTabBtn"]` |
| Charts | only the `--chart-*` tokens |

Examples live in `themes.css` itself: **Cyberpunk** (neon, corner marks on buttons), **Warhammer 40k** (bronze, rivets,
a background image) and six more themes between `/* === THEME: ... === */` markers (SCP Dossier, Workbench, Synthwave,
JRPG, Model Manual, Hardware PCB). A new theme is easiest to start from a copy of the closest one in spirit.

## 6. Minimal skeleton

```css
/* === THEME: theme-neon === */
body.theme-neon {
    /* charts */
    --chart-font: 'Orbitron', ui-monospace, monospace;
    --chart-text: #f5e9ff;
    --chart-muted: #b8a0d9;
    --chart-grid: rgba(255, 79, 216, 0.18);
    --chart-stage-1: #3a2f5a;
    --chart-stage-2: #4fd1ff;
    --chart-stage-3: #ffd84f;
    --chart-stage-4: #ff4fd8;
    --chart-stage-5: #9b6bff;
    --chart-stage-6: #4fffb0;
    --chart-stage-7: #ffb04f;
    --chart-series-1: #4fd1ff;
    --chart-series-2: #ff4fd8;
    --chart-border: transparent;
    --chart-border-width: 0;
    --chart-radius: 4;
    /* page */
    background-color: #0d0a1a !important;
    color: #f5e9ff !important;
    font-family: 'Orbitron', ui-monospace, monospace !important;
}
body.theme-neon nav { background: #140f2a !important; border-bottom: 1px solid #ff4fd8 !important; }
body.theme-neon .bg-slate-800 { background: #1a1433 !important; border-color: #3a2f5a !important; }
body.theme-neon .bg-slate-900 { background: #120d26 !important; }
body.theme-neon select, body.theme-neon select option { background-color: #120d26 !important; color: #f5e9ff !important; }
body.theme-neon button.bg-indigo-600 { background: #7a2cff !important; }
/* ... buttons, deadlines, modals ... */
/* === END THEME: theme-neon === */
```

(Orbitron has no Cyrillic; it is used here only to keep the skeleton short. A real theme needs a Cyrillic font.)

## 7. Checking

1. `python tools/check_theme.py theme-neon`. Errors (✗) must be fixed, warnings (!) reviewed.
2. Open the app, pick the theme in the dropdown in the header and go through the screens:
   - dashboard: project cards with every kind of deadline (normal, due soon, overdue, closed);
   - the "Upcoming tasks" column and the "All tasks" tab;
   - the "Calendar" and "Feed" (progress photos) tabs;
   - the "Hall of Fame & Analytics" tab: both charts and the cards of finished projects;
   - the project card (modal): tasks, photos, references, paint recipe, pipeline template choice; the trash window;
   - the "New project" and "New task" windows, the delete confirmation, toasts;
   - settings: all seven tabs;
   - the `/login` page, if login is on;
   - switching to another theme and back: the charts must recolor right away, without a reload.
3. Check in English (Settings → General → Interface language): labels are longer and must not break the frames.

## 8. Common mistakes

- Forgot `!important`: some elements stay standard.
- A selector without `body.theme-<name>`: the theme "leaks" into the others.
- A font without Cyrillic: Russian text is drawn with a fallback font and the look falls apart.
- Forgot one of the `--chart-*` tokens: the chart takes the standard color; `check_theme.py` reports it.
- Light text on a light background in dropdowns: set both `select` and `select option`.
- Fixed width or height of plates: dates with the year and English labels are longer.
- The theme is not in the `THEMES` registry: it will not appear in the list.
- A theme name with capitals or underscores: only lowercase Latin letters, digits and hyphens are allowed.

## 9. A ready prompt for an AI model

Copy the text below, add a description of the style and attach a screenshot of the interface, `static/themes.css`
(as an example) and this file.

```
You are making a UI theme for a web app (FastAPI + Tailwind, dark interface, Russian and English UI).
Theme style: <DESCRIBE THE STYLE, COLORS, MOOD, FONTS>.
Theme name: theme-<name> (lowercase Latin letters/digits/hyphens).

Read the attached THEMES_GUIDE.en.md and follow section 3 "Rules" literally. The examples are Cyberpunk and Warhammer
in the attached themes.css.

Output exactly three code blocks and nothing else, except one line of explanation for each:
1) CSS for the end of static/themes.css between the markers /* === THEME: theme-<name> === */ and /* === END THEME: theme-<name> === */.
   Every selector starts with body.theme-<name>; every property has !important (except --variables); all 15 --chart-*
   tokens are set; body has background-color and color; fonts must include Cyrillic.
2) One line for the THEMES registry in static/app.js: { id: 'theme-<name>', label: () => '<emoji> <Title>' },
3) The web fonts with weights as Fontsource package names (for example "russo-one": (400,)), or the word "none".

Do not: change the layout or sizes, hide elements, use external images or @import, touch selectors without the theme
prefix. Dates in the plates look like 21.03.2027; do not give plates a fixed width.
Do not invent background images: use gradients and CSS patterns, or name a file that I will put into static/.
```

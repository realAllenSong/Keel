# Keel design: 宣纸编辑部 (ink and paper editorial)

Chosen 2026-11-08 from three generated directions (reference renders: the "A" screens in the
review page). Keel reads like a carefully typeset letter about your money: rice paper, sumi ink,
and one vermilion seal colour, used only where something needs you.

## Identity

- **Mark**: one ink brushstroke rising toward a vermilion sun. App icon: `desktop/assets/KeelArtwork.png`.
  In-app mark (`KeelMark`): the stroke as a mask from `apple/Assets/KeelStroke.png`, tinted with
  `Theme.ink` (so it turns light in dark mode), with the sun drawn as a vector circle in `Theme.signal`.
- **Voice**: every screen opens with one plain sentence that answers its question
  ("11 月到今天：收入 $1,900，支出 $13,020"), then the evidence.

## Colour

| Token | Light | Dark | Use |
|---|---|---|---|
| paper | `#F4EFE8` | `#15130F` | the page |
| card | `#FBF8F3` | `#1C1915` | the rare raised surface (sheets, inspectors) |
| ink | `#1A1614` | `#EFE9E1` | text, the series line, the mark |
| muted | `#615955` | `#A0968C` | secondary text |
| rule | `#C7BFBA` | `#3A352E` | hairlines between sections |
| signal | `#C2412D` | `#E0745D` | vermilion: actions to take (结账), the low point, today |

Vermilion is a seal, not a theme colour: at most one or two places per screen.

## Type

- **Display** `Font.serif`: Charter for Latin and figures, falling back to Songti SC (宋体) for
  Chinese. Headlines, section titles, the one hero figure per section.
- **UI** `Font.ui`: Geist (bundled), PingFang for Chinese.
- **Figures in columns** `Font.mono`: Geist Mono, tabular.

Scale: headline 40, section title 22, hero figure 44, body 14, caption 12.

## Layout

- One calm reading column (max 1232), generous margins, sections separated by hairline rules rather
  than cards. Cards only where something is a separate object.
- Sections are numbered exhibits ("01 / 本月支出") because the page is read in order.
- The status strip under the headline holds what needs you now (结账, files waiting); it is empty
  when nothing does.
- Charts are ink lines on paper: one ink series, a quiet grey comparison, vermilion for the point
  that matters.

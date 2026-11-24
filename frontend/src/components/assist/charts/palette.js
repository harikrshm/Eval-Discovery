// One palette for every console chart. Values come from the dataviz skill's reference palette
// (references/palette.md), first six categorical slots, in the skill's fixed order. The order is the
// colour-blind-safety mechanism: assign by series position, never cycle, never add a seventh.
//
// Validator run (dataviz scripts/validate_palette.js), console surface is the white card (#ffffff):
//
//   $ node validate_palette.js "#2a78d6,#eb6834,#1baf7a,#eda100,#e87ba4,#008300" --mode light --surface "#ffffff"
//   Palette (light, surface #ffffff, categorical): 6 slots
//     [PASS] Lightness band         all 6 inside L 0.43-0.77
//     [PASS] Chroma floor           all 6 >= 0.1
//     [PASS] CVD separation         worst adjacent #eda100<->#1baf7a dE 9.1 (protan) . tritan 5.8
//     [PASS] Normal-vision floor    worst adjacent #e87ba4<->#eda100 dE 19.6 (normal)
//     [WARN] Contrast vs surface    below 3:1 - relief required (visible labels or table view):
//            #1baf7a 2.82, #eda100 2.17, #e87ba4 2.69
//     -> ALL CHECKS PASS (exit 0)
//
//   $ ... --pairs all     (scatter / small multiples; NOT how the console uses the palette)
//     [FAIL] CVD separation         worst all-pairs #008300<->#eb6834 dE 3.2 (protan)
//     [FAIL] Normal-vision floor    worst all-pairs #e87ba4<->#eb6834 dE 12.9
//     -> FAILED (exit 1). Consequence: the console only draws lines and bars, where only
//        neighbouring series touch, so the adjacent list applies. Do not use SERIES for
//        scatter, bubble or map charts; chart tabs should keep to four series or fewer and
//        fold the rest into "Other".
//
// Relief for the WARN: every chart shows its series names as legend text and has a "View as table"
// text equivalent (ChartCard), and every bar carries a value label. Series are never told apart by
// colour alone. Only the light surface exists in this app (no dark theme), so no dark run.
//
// Status colours (good / bad / neutral) are reserved for state; they are never series colours and
// always ship with an arrow icon and a word (KpiCard). Contrast on #ffffff, computed with the same
// WCAG formula as the validator: good #0ca30c 3.35 (icon only), goodText #006300 7.54,
// bad #d03b3b 4.80, neutral #6b6b6b 5.33, tick text #6b6b6b 5.33, focus #4338ca 7.90.
// Grid #e1e0d9 and axis line #c3c2b7 are deliberately recessive (hairlines, no text).

export const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"];

export const MAX_SERIES = SERIES.length;

export const STATUS = {
  good: "#0ca30c",
  bad: "#d03b3b",
  neutral: "#6b6b6b",
};

export const GOOD_TEXT = "#006300";

export const FOCUS = "#4338ca";
export const GRID = "#e1e0d9";
export const AXIS_LINE = "#c3c2b7";
export const TICK_TEXT = "#6b6b6b";
export const SURFACE = "#ffffff";
// The neutral grey of "Other" (the folded tail of a chart with more than six series).
export const OTHER = "#898781";
// A marker (a prompt release, a demo boundary) is ink, not a series colour.
export const MARKER = "#52514e";

// The colour of series number `index`, unless the caller pinned one. Colour follows the entity: a
// caller that filters series must pass the original colour (or keep the original order).
// Past the sixth series the colours are never cycled: the tail is drawn in the neutral "Other" grey.
export function seriesColor(index, override) {
  return override ?? SERIES[index] ?? OTHER;
}

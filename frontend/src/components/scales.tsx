/**
 * Shared chart scales.
 *
 * Deliberately free of a "use client" directive so a server component can import
 * these without pulling a client module across the boundary — importing even a plain
 * constant from a client module left the value undefined at render, and every bar
 * came out `width: NaN%`, which the browser drew as full width.
 */

/**
 * Full-bar value for every degradation chart, in seconds per lap.
 *
 * A FIXED scale, not one derived from whatever the current view happens to contain.
 * Scaling to the local maximum makes every race look the same: 0.05 s/lap at a gentle
 * circuit draws the same bar as 0.20 s/lap at a harsh one, so two races cannot be
 * compared — which is most of the point of having the chart.
 *
 * 0.22 s/lap is the upper physical bound from Kolbe et al., the same constant the
 * stint fit uses to reject implausible slopes, so a full bar means "as bad as tyres
 * get". Shared because the per-race chart and the season circuit table draw the same
 * quantity and must not invent two scales for it.
 */
export const DEGRADATION_SCALE_MAX = 0.22;

/**
 * Shared card surface treatment for logic-editor function blocks (issue #1074).
 *
 * Every node component paints its category colour as a translucent tint on top
 * of the opaque `--node-card-bg` theme surface. The surface itself is applied
 * through the global `.logic-node-surface` class in `style.css`; the components
 * only pass the tint in via the `--node-tint` inline custom property.
 *
 * Keeping the alpha suffix here means all blocks — generic, datapoint, python,
 * comment, missing — stay visually consistent when it is tuned.
 */

/** Hex alpha suffix (`#rrggbbaa`) for the category tint — ~7 % opacity. */
export const NODE_TINT_ALPHA = '12'

/**
 * Build the `--node-tint` value for a category colour.
 *
 * @param {string} color 6-digit hex colour, e.g. `#1d4ed8`
 * @returns {string} 8-digit hex colour with the shared tint alpha
 */
export const nodeTint = (color) => `${color}${NODE_TINT_ALPHA}`

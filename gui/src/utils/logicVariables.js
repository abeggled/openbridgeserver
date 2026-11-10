/**
 * Shared ###VAR### variables of opted-in logic block fields (issue #1301).
 * Mirrors obs/logic/variables.py — keep both lists in sync.
 */

export const LOGIC_DATE_VARIABLES = [
  'H', 'HH', 'm', 'mm', 's', 'ss', 'd', 'dd',
  'EE', 'EEE', 'EEEE', 'M', 'MM', 'MMM', 'MMMM', 'yy', 'yyyy',
]
export const LOGIC_STANDARD_VARIABLES = ['DATE', 'TIME', 'TS']

const KNOWN = new Set([...LOGIC_DATE_VARIABLES, ...LOGIC_STANDARD_VARIABLES])
const PLACEHOLDER_RE = /###([A-Za-z][A-Za-z0-9_]*)###/g

/** `###NAME###` token for a variable name. */
export function variableToken(name) {
  return `###${name}###`
}

/** Slot numbers of a block's configured `variables` list (only those with an object). */
export function configuredObsSlots(variables) {
  if (!Array.isArray(variables)) return []
  return variables
    .map((v, i) => {
      const slot = Number.parseInt(v?.slot, 10)
      return { slot: Number.isInteger(slot) && slot > 0 ? slot : i + 1, configured: !!v?.datapoint_id }
    })
    .filter(v => v.configured)
    .map(v => v.slot)
}

/**
 * Placeholders in `text` the backend would not resolve: unknown names stay
 * literal, and OBS<n> without a configured slot is an error at run time.
 *
 * @param {string} text
 * @param {number[]} obsSlots configured OBS slot numbers
 * @returns {string[]} offending names, unique, in order of appearance
 */
export function unresolvedVariables(text, obsSlots = []) {
  const bad = []
  for (const match of String(text ?? '').matchAll(PLACEHOLDER_RE)) {
    const name = match[1]
    const obs = /^OBS([1-9][0-9]*)$/.exec(name)
    const ok = obs ? obsSlots.includes(Number(obs[1])) : KNOWN.has(name)
    if (!ok && !bad.includes(name)) bad.push(name)
  }
  return bad
}

/** Normalise a block's `variables` config (array or JSON string) to `[{slot, datapoint_id, datapoint_name}]`. */
export function normaliseObjectVariables(raw) {
  let variables = raw
  if (typeof variables === 'string') {
    try {
      variables = JSON.parse(variables)
    } catch {
      variables = []
    }
  }
  return Array.isArray(variables)
    ? variables.map((v, i) => {
        const slot = Number.parseInt(v?.slot, 10)
        return {
          slot: Number.isInteger(slot) && slot > 0 ? slot : i + 1,
          datapoint_id: v?.datapoint_id || '',
          datapoint_name: v?.datapoint_name || '',
        }
      })
    : []
}

/** Block types whose configuration carries a `variables` list (#1301). */
export const VARIABLE_BLOCK_TYPES = [
  'api_client', 'json_extractor', 'xml_extractor', 'string_concat', 'string_replace',
  'message_archive', 'notify_message', 'ical',
]

// Server issue messages (obs/logic/variables.py, executor.py) → i18n keys. The server texts
// are stable English prose; unknown messages are shown verbatim.
const ISSUE_PATTERNS = [
  [/^unknown variable (###.+###)$/, 'unknownVariable', ['name']],
  [/^path '(.*)' not found$/, 'pathNotFound', ['path']],
  [/^invalid XPath '(.*)': (.*)$/, 'invalidXPath', ['path', 'error']],
  [/^(?:[^;]*? )?variable (OBS\d+) is not configured$/, 'notConfigured', ['name']],
  [/^(?:[^;]*? )?variable (OBS\d+) object (.*) is not available$/, 'notAvailable', ['name', 'object']],
  [/^(?:[^;]*? )?variable (OBS\d+) references an invalid object$/, 'invalidObject', ['name']],
  [/^(?:[^;]*? )?variable (OBS\d+) object (.*) has no value$/, 'noValue', ['name', 'object']],
  [/^(?:[^;]*? )?variable value is empty$/, 'emptyValue', []],
]

// A block's ``__error__`` may join several issues with "; ". Only split where the next segment
// starts a new issue, so a "; " inside a user-defined object name stays part of that issue.
const ISSUE_BOUNDARY_RE = /; (?=unknown variable |path '|invalid XPath |(?:[^;]*? )?variable (?:OBS\d+|value is empty))/

export function translateVariableErrors(text, t) {
  if (typeof text !== 'string') return text
  return text.split(ISSUE_BOUNDARY_RE).map(part => translateVariableIssue(part, t)).join('; ')
}

export function translateVariableIssue(issue, t) {
  const text = String(issue)
  for (const [re, key, names] of ISSUE_PATTERNS) {
    const m = re.exec(text)
    if (m) return t(`logic.variables.issues.${key}`, Object.fromEntries(names.map((n, i) => [n, m[i + 1]])))
  }
  return text
}

/**
 * Whether a DPT already set keeps winning over a DPT taken over from the group address
 * catalog (#1260). A catalog DPT that names only the main type ("DPT5") does not replace
 * a subtype of that main type ("DPT5.001"): the values would change, e.g. by a factor of
 * 2.55 for DPT5. A subtype, a different main type or an empty field take the new DPT.
 *
 * Same rule as keeps_stored_subtype() in obs/adapters/knx/dpt_registry.py (the .knxproj
 * re-import); tests/fixtures/dpt-keep-parity.json, generated from Python, is the contract.
 */
export function keepsStoredSubtype(next, stored) {
  return Boolean(next && stored && !next.includes('.') && stored.startsWith(`${next}.`))
}

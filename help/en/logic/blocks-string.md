---
title: "Blocks: Text"
---

# Blocks: Text

Text-processing blocks, plus a purely visual documentation block.

## String Concat {#logic-block-string-concat}

Concatenates 2–20 texts into a single result. Each input can either be connected dynamically via
an edge or pre-filled as **static text** directly in the config panel — if an input is connected,
the incoming value takes precedence over the static text. Empty inputs/fields produce an empty
substring. Optionally set a **separator** between the parts (empty = none).

The static texts support the variables `###HH###`, `###DATE###`, `###TS###`, etc. as well as
`###OBS1###`, `###OBS2###` … for objects assigned under **Variables** (list: [JSON Extractor](./blocks-integration#logic-block-json-extractor)).
Values connected through inputs are never evaluated. An empty or unconfigured `###OBSn###`
slot yields no result and a hint in the configuration panel.

## String Replace {#logic-block-string-replace}

Replaces matches in a text using an ordered list of rules — rules are applied top to bottom in
order, each rule working on the result of the previous one. Per rule:

- **Mode** — plain search text, or a regular expression (RegEx); with RegEx, group references
  like `\1` or `\g<name>` are usable in the replace field.
- **Case sensitive** and **Replace all occurrences** (instead of only the first).
- An empty replace field removes the matches without substitution.
- Variables are allowed in the **replace** text (`###HH###`, `###DATE###`, `###OBS1###` …, see
  [JSON Extractor](./blocks-integration#logic-block-json-extractor)). In RegEx rules substituted values stay literal; group references only work in your own
  text. An unresolvable `###OBSn###` slot yields no result and a hint.

Rules can be reordered, added, and removed via the arrow buttons.

## Comment {#logic-block-comment}

Free multi-line text for documentation directly on the canvas — purely visual, has no effect on
graph execution whatsoever. The comment block can be resized directly on the canvas by dragging
its corner (no setting in the config panel needed).

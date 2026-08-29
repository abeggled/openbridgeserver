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

## String Replace {#logic-block-string-replace}

Replaces matches in a text using an ordered list of rules — rules are applied top to bottom in
order, each rule working on the result of the previous one. Per rule:

- **Mode** — plain search text, or a regular expression (RegEx); with RegEx, group references
  like `\1` or `\g<name>` are usable in the replace field.
- **Case sensitive** and **Replace all occurrences** (instead of only the first).
- An empty replace field removes the matches without substitution.

Rules can be reordered, added, and removed via the arrow buttons.

## Comment {#logic-block-comment}

Free multi-line text for documentation directly on the canvas — purely visual, has no effect on
graph execution whatsoever. The comment block can be resized directly on the canvas by dragging
its corner (no setting in the config panel needed).

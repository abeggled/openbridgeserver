---
title: Icons
---

# Icons

The Icons tab lives in the Admin GUI under **Settings → Icons**.

## Icon library {#settings-icons}

Shows all installed icons as a searchable grid. Multi-select by clicking enables export (as a
collection) or deletion of the selected icons; "Select all"/"Deselect all" act on the
currently filtered/searched list.

## Import SVG icons {#settings-icons-import}

Upload icons via drag & drop or file selection — individual SVG files or a ZIP archive
containing multiple SVGs. Every file is validated as proper SVG regardless of its file
extension.

## Import KNX UF iconset {#settings-icons-knxuf}

Downloads all icons from the `ha-knx-uf-iconset` project directly from its GitHub
repository and stores them in the icon library with a `kuf_` prefix. Any `kuf_` icons already
present from an earlier import are overwritten.

## Import FontAwesome {#settings-icons-fontawesome}

Imports icons directly from FontAwesome 7. Without an API key, free-tier icons are used; with
a (persistently stored) FontAwesome PRO API key, PRO icons and additional styles (Light,
Thin, Duotone alongside Solid/Regular/Brands) also become available. Icon names are entered
comma-separated; the imported filename automatically includes the chosen style (e.g.
`abacus-solid.svg`).

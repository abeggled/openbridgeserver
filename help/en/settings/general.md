---
title: General Settings
---

# General Settings

You'll find the general settings in the Admin GUI under **Settings → General**.

## Timezone, date and time format {#settings-general}

**Timezone** — all timestamps shown throughout the system use the timezone selected here:
data point timestamps, history, the ring buffer, and log entries. The search field filters
the list by place name or abbreviation (e.g. "Zurich", "Berlin", "UTC").

**Default date format** and **default time format** — freely definable format strings using
these tokens:

| Token | Meaning |
|---|---|
| `d` / `dd` | Day |
| `EE` / `EEE` / `EEEE` | Weekday short / medium / long (e.g. "Mo", "Mon", "Monday") |
| `M` / `MM` / `MMM` / `MMMM` | Month |
| `yy` / `yyyy` | Year |
| `H` / `HH` | Hour |
| `m` / `mm` | Minute |
| `s` / `ss` | Second |

**Regional format** — determines how numbers, currency amounts, and dates are formatted
(decimal separator, thousands separator, day/month/year order). The regional format is
deliberately **independent of the interface language** — German in Switzerland formats
numbers differently than German in Germany (e.g. `1'234.50` vs. `1.234,50`). "Auto" derives
the format from the current interface language; any other choice explicitly overrides that.

**Currency** — determines the currency symbol/code shown for monetary amounts. "Auto" derives
the currency from the selected regional format.

The preview below the dropdowns immediately shows how a sample number and a sample amount
would be formatted with the current combination of regional format and currency — before
saving.

## Appearance and language {#settings-appearance}

**Appearance** — controls the color scheme of the user interface:

- **System** — follows the operating system/browser setting and switches automatically when
  that changes.
- **Light** — always the light color scheme.
- **Dark** — always the dark color scheme.

**Language** — switches the language of the user interface itself (menus, labels, messages).
This is a separate setting, independent of the regional format above — you can, for example,
set the interface to English while still displaying numbers in Swiss format.

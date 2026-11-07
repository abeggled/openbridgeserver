---
title: Updates
---

# Updates

You find the Updates tab in the Admin GUI under **Settings → Updates**.

## Update check and update channel {#settings-updates}

OBS checks on its own whether a newer version than the installed one is available. If so, a
notice appears on every page of the Admin GUI with the channel, the installed and the latest
version, the time of the last successful check and a **Release notes** button. Without an
available update there is no notice; the current status stays visible in this tab.

The check only detects updates — installing one still goes through the existing path
(`obs-update` or a new Docker image).

**Update channel** — decides which versions count as an update:

| Channel | Reports |
|---|---|
| Stable | regular releases only (e.g. `2026.11.0`) |
| Pre-release | regular releases and release candidates (e.g. `2026.11.0-RC1`), but no nightlies |
| Nightly | daily nightly builds only (`nightly-YYYYMMDD`) |

A nightly is never reported as a stable or pre-release update. On installation, or on the
first start with this feature, OBS picks the channel that matches the installed version and
adjusts it when the installation later moves, e.g. via `obs-update` from a nightly to a regular
release. A channel an administrator chose stays as chosen; only administrators can change it. Draft releases and tags outside this scheme are ignored.

**Check interval** — after every start the first check runs after a random offset of 0 to 719
minutes, then every twelve hours, so the requests of many installations spread evenly. An
administrator can trigger a check right away with **Check now**. The request runs on the
server, not in the browser, and its result is cached.

**Errors** — if a check fails (for example without internet access), the last successful
result is kept and marked as possibly out of date. OBS only tries again at the next regular
time. Installations without internet access can turn the online check off in `config.yaml`:

```yaml
update_check:
  enabled: false
```

**Release notes** — the popup shows only the versions of the active channel that are newer
than the installed version and not newer than the latest one, in chronological order and
separated per version.

### System data points

For the Visu and Logic, OBS automatically creates five data points that can be read like any
other data point but cannot be renamed, duplicated, deleted or written (neither through the
Admin GUI nor through the API, MQTT, adapters or Logic — a logic sheet can read them but cannot
use them as the target of a write block or a value sequence). Their IDs are the same on every
installation, so Visu and Logic configurations stay portable.

| Data point | Type | Content |
|---|---|---|
| OBS Update Channel | STRING | `stable`, `pre-release` or `nightly` |
| OBS Current Version | STRING | installed version |
| OBS Latest Version | STRING | latest version in the active channel |
| OBS Last Check | DATETIME | time of the last completed check attempt |
| OBS Update Available | BOOLEAN | `true` when an update is available in the active channel |

Channel and installed version are available right after start-up. **OBS Latest Version** and
**OBS Update Available** keep the quality `uncertain` until the first successful check, so no
false "no update" is reported. A system data point may be passed on to devices through DEST
bindings (e.g. to a KNX group address), but cannot receive a SOURCE binding. The timer and the
presence simulation cannot use a system data point. A new binding receives the current value
right away, and so does every adapter instance as soon as it is (re)connected. A KNX project or CSV import creates no additional data
point for a group address a system data point sends to.

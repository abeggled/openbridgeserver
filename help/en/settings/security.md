---
title: Security
---

# Security

The Security tab lives in the Admin GUI under **Settings → Security**. This tab is only
visible and usable for administrators.

## URL Target Allowlist {#settings-security}

Logic graphs and API proxies are allowed to call public HTTP/HTTPS targets directly. Internal,
private, or reserved IP ranges (e.g. `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) are
blocked by default instead — this protects against SSRF (Server-Side Request Forgery), where a
logic graph or adapter could otherwise be abused to reach internal services on your network
that were never meant to be reachable from outside.

Anyone who deliberately wants to reach an internal target (e.g. another OBS instance, or
another trusted system on the same network) must explicitly allow it in this allowlist.
Allowing a target lets the backend actively request that internal address — incorrectly
configured entries can weaken SSRF protection and network segmentation.

The allowlist is stored as a YAML file on the server (path shown in the card), not in the
database.

## Check target {#settings-security-check}

Tests whether a given URL would currently be allowed or blocked — without having to run a
logic graph or trigger an adapter call to find out. The result shows the resolved IP
addresses and, if blocked, a reason. If the target is blocked, the suggested target can be
added to the allowlist directly from the result.

## Allowed targets {#settings-security-entries}

Manages the allowlist entries by hand: target (host, IP, or CIDR range, e.g.
`10.38.113.23/32`) plus an optional reason for traceability. Entries can be removed again at
any time; "Reload" fetches the current state of the YAML file from the server again.

---
title: API Keys
---

# API Keys

API Keys live in the Admin GUI under **Settings → API Keys**.

## Managing API Keys {#settings-apikeys}

An API Key allows programmatic access to the REST API without using a username and password.

**+ API Key** — give it a name (free text, e.g. "Home Assistant") and it generates the key.
**The key is shown only once, right after creation** — after that it can no longer be
retrieved, only a new one created.

**Delete** — revokes the key immediately; any running integration still using it loses access.

**Capabilities (administrators only)** — restricts a single key to a subset of the available
API capabilities, independent of the permissions of the user who created it. Changes must be
explicitly confirmed before they can be saved; if someone else saves the same capability list
in the meantime, that is detected and the change is rejected instead of silently overwriting
the other change.

---
title: Users
---

# Users

User management lives in the Admin GUI under **Settings → Users**. This tab is only visible
and usable for administrators.

## Managing users {#settings-users}

**New user** — sets a username, password, and optionally admin rights. MQTT access can be
enabled right away when creating the user (see below).

Each row in the list shows:

- **Status** — "Current account" for your own account, "MQTT without password" (warning) when
  MQTT is enabled but no MQTT password has been set yet, otherwise "Ready".
- **Scopes** — a summary of the hierarchy scopes and roles assigned via the rights editor (see
  the next section).
- **MQTT** — whether the user can log in to the MQTT broker, and whether a separate MQTT
  password has been set for that. The MQTT password is independent of the login password.

**Set/remove MQTT password** — via the wrench and trash-can icons on each row. Without a set
password, the user cannot log in to the broker even with MQTT access enabled.

**Delete user** — not possible for your own account. Before deleting, the impact is checked:
visu pages, logic graphs, and ring buffer filtersets can be transferred to a **successor**; API
keys, however, are **always revoked immediately** (never transferred), since only the previous
owner could know the associated secret.

## Rights editor {#settings-users-rights}

"Edit rights" (only shown once at least 2 users exist) opens a multi-step editor for a user's
hierarchy permissions:

1. **Role** — one of four base roles: **Guest** (read-only), **Resident** (read, write,
   activate), **Operator** (full operational access including create), or **Owner** (all
   actions). Each role starts from its stored direct assignments — switching to a newly
   selected role starts with no scopes.
2. **Scopes** — for each hierarchy scope, decide whether the role is **inherited** (taken from
   the parent scope), explicitly **allowed**, or explicitly **denied**. A direct deny on a
   scope is preserved while editing and cannot be overridden here.
3. **Preview** — shows the actually computed permissions (allowed/denied) per action and scope,
   with a reason, before saving.
4. **Confirm** — final confirmation of the change.

Separately, per user, you can also grant permission to create, import, and duplicate new logic
graphs — including a dedicated option for logic graphs that control central-plant equipment.

"""Built-in Logic function blocks, grouped by node category.

One module per function block; one sub-package per node category. The
sub-package name is always identical to the ``category`` field of the node
definitions it contains, and every sub-package exposes its own
``NODE_TYPES`` tuple. ``obs.logic.registry`` only combines those tuples into
the global catalogue.

This package intentionally has no import side effects: it must not import the
category packages, so a single node module can be imported (and reviewed) on
its own. See ``docs/architecture/logic-nodes.md`` for the dependency rules and
the procedure for adding a new function block.
"""

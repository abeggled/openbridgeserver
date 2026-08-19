# Creation authority contract

This document records how authorization is derived before a new resource has
its own persisted scope. Creation is default-deny: authority must come from an
existing parent/container or from a closed capability declared in code.

| Surface | Creation authority | Resulting authority | Additional boundaries |
|---|---|---|---|
| Adapter instance | Admin/owner only | Existing instance lifecycle rules | No delegated root creation |
| Adapter-owned DataPoint or binding operation | `WRITE` on the existing adapter instance and every capability declared for the operation | Existing DataPoint/binding policy; a delegated imported DataPoint receives an `operator` grant for its creator | User principals only; unknown, undeclared, and KNX adapters fail closed |
| Logic create/import | `GENERATE` on the closed `create_graph` Logic capability | Atomic `operator` grant on the new graph | User principals only; delegated graphs start disabled; `ACTIVATE`, side-effect capabilities, DataPoint scope, and `central_control` remain separate checks |
| Logic duplicate | Read access to the source graph plus the same closed Logic creation capability | Same as Logic create/import | The source graph remains concealed when it is not readable |
| Visu create | `GENERATE` on the existing target parent | Normal Visu inheritance from that parent | Root creation is admin-only; referenced DataPoints require `GENERATE`; inherited user audiences must retain DataPoint `READ` |
| Visu copy | Read access to the source plus `GENERATE` on the target parent | Normal Visu inheritance from the target parent | Source and target concealment apply; referenced DataPoints require `GENERATE` |
| Visu import | `GENERATE` on the existing target parent | Normal Visu inheritance from the target parent | The complete subtree is validated before the first write and persisted atomically |

API keys do not receive any of these creation authorities. Their existing,
explicit configuration capabilities remain limited to the code-defined Visu
page-config and DataPoint-metadata mutations.

MQTT explicitly declares adapter delegation capabilities. KNX and every
adapter without an explicit declaration remain non-delegable. A declaration
does not create an API route by itself; it only authorizes a matching existing
adapter-owned operation after the instance-scope check succeeds.

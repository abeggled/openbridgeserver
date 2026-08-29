---
title: "Blocks: Script"
---

# Blocks: Script

## Python Script {#logic-block-python-script}

Runs a Python script in a restricted sandbox. The values of the three inputs **IN 1**/**IN
2**/**IN 3** are available in the script via the `inputs` dictionary (e.g. `inputs['in1']` or
`inputs.get('in2', 0)`); the script returns its result by setting the `result` variable — its
value appears on the **Result** output.

The sandbox only allows simple expressions and assignments plus the `math` module; imports,
class/function definitions, lambdas, `try`/`with`, and attribute access other than on `math.*`
are all disallowed. Available built-in functions: `range`, `len`, `int`, `float`, `str`,
`bool`, `abs`, `min`, `max`, `round`.

The block requires the **Python execution** permission; without it, the Logic graph is blocked
at the run preflight.

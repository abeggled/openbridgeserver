---
title: "Bausteine: Skript"
---

# Bausteine: Skript

## Python Script {#logic-block-python-script}

Führt ein Python-Skript in einer eingeschränkten Sandbox aus. Die Werte der drei Eingänge
**IN 1**/**IN 2**/**IN 3** stehen im Skript über das Dictionary `inputs` zur Verfügung (z. B.
`inputs['in1']` oder `inputs.get('in2', 0)`); das Skript liefert sein Ergebnis, indem es die
Variable `result` setzt — deren Wert erscheint am **Ergebnis**-Ausgang.

Die Sandbox erlaubt nur einfache Ausdrücke und Zuweisungen sowie das `math`-Modul; nicht
erlaubt sind u. a. Imports, Klassen-/Funktionsdefinitionen, Lambdas, `try`/`with`, sowie
Attributzugriffe ausser auf `math.*`. Verfügbare eingebaute Funktionen: `range`, `len`, `int`,
`float`, `str`, `bool`, `abs`, `min`, `max`, `round`.

Der Baustein erfordert die Berechtigung **Python-Ausführung**; ohne diese Berechtigung wird der
Logik-Graph beim Ausführungs-Preflight blockiert.

# Issue 864 i18n smoke test

Purpose: help reviewers verify that raw i18n keys and raw `$t(...)` expressions no longer appear in the affected Admin GUI and Visu configuration paths.

## Reviewer checks

1. Start the Admin GUI and open a data point binding form.
2. Verify the binding enabled checkbox label is translated:
   - English: `Enabled`
   - German: `Aktiviert`
3. Open the Visu editor and add or configure an Info widget.
4. Verify the extra values heading is translated:
   - English: `Additional values (max. 6)`
   - German: `Zusätzliche Werte (max. 6)`
5. Confirm the literal text `$t('widgets.info.additionalValues', { max: MAX_EXTRA })` is not visible.
6. Run the automated regression checks:

```bash
tools/with-venv pytest tests/unit/test_check_i18n_guard.py
cd frontend && npm run test -- src/widgets/Info/Config.test.ts
```

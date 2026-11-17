# Data-quality audit

Source: `household_power_consumption.txt`
SHA-256: `4259c9d7ece5dbee9ab8d53682baac68d791c864f0f64a52b4043cb3b90894b7`

| Check | Count |
| --- | ---: |
| source rows | 2,075,259 |
| duplicate rows | 0 |
| duplicate timestamps | 0 |
| invalid timestamps | 0 |
| off minute timestamps | 0 |
| missing measurement rows | 25,979 |
| all measurements missing rows | 25,979 |
| missing measurement cells | 181,853 |
| invalid numeric cells | 0 |
| negative cells | 0 |
| infinite cells | 0 |
| absent timestamp rows | 0 |
| missing run count | 71 |
| missing minutes in runs | 25,979 |
| longest missing run minutes | 7,226 |

Missing-measurement source rows: 1.2518%.
Runs longer than one day: 6.
Exception records: 181,853 (multiple exceptions may describe one row).

SQL/Python totals, monthly totals, and every missing run reconcile exactly.
No values were imputed. These are data-quality exceptions, not model anomaly alerts.
See docs/data_quality_audit.md for definitions, limits, and the proposed missing-data policy.

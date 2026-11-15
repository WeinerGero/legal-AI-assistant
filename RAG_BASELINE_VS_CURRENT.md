# Baseline versus current

Baseline commit: `6f11e3ba3bfbc4ef6b3ab738f407b5c3aa1bbdfa`. Current commit: `ee63dc2365aca526cfda4df217c70ea834ca7942`.

| Metric | Baseline | Current | Delta |
|---|---:|---:|---:|
| Full live cases | historical artifacts vary | P02 5/5; mixed progress 10/65 | N/A |
| Codex score | P01 0.5675 | P02 0.8525 | +0.2850 (+50.22%) |
| Required Fact Recall | P01 7/15 (46.67%) | P02 15/20 (75.00%) | +28.33 pp |
| Citation Coverage | P01 3/4 (75%) | P02 5/5 (100%) | +25.00 pp |
| Technical Refusal Rate | P01 0/5 | P02 0/5 | 0 pp |
| False Refusal Rate | P01 1/4 (25%) | P02 0/5 (0%) | -25.00 pp |

Only identical case IDs may be compared; unavailable historical values remain N/A.

P01 and P02 contain different IDs and different commits, so the deltas above
describe adjacent partitions rather than a causal matched-case experiment.
The organizer result (10.083 points, 700.8 seconds) remains an external control
point and is not converted to the Codex scale.

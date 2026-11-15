# Golden evaluation report

- Golden cases: 65
- Full real pipeline completed across preserved partitions: 10/65 (mixed-version view)
- Current-version P02 completed and evaluated: 5/5
- Historical P01 completed and evaluated: 5/5
- P02 Codex mean score: 0.8525
- Mixed P01+P02 Codex mean score: 0.7100 (10 cases; not a single-build score)
- Status: in progress
- Reason: none
- Commit: `ee63dc2365aca526cfda4df217c70ea834ca7942`
- Golden SHA-256: `dc4143f78f9978217135e66208e94102fed90e2b6ef858cd2d6f3a8ed062da13`
- Corpus SHA-256: `559a669df13fa104dae32714745ad995f584c15b923f8c9bb5b618e533249693`

The Codex score is an internal diagnostic assessment, not the organizer score. All unavailable quality measures are N/A, not zero.

| Metric | P01 historical | P02 current | Mixed 10 cases |
|---|---:|---:|---:|
| Codex score | 0.5675 (5) | 0.8525 (5) | 0.7100 (10) |
| Required Fact Recall | 7/15 (46.67%) | 15/20 (75.00%) | 22/35 (62.86%) |
| Claim Support Rate | 13/13 (100%) | 20/20 (100%) | 33/33 (100%) |
| Citation Coverage | 3/4 (75%) | 5/5 (100%) | 8/9 (88.89%) |
| Technical Refusal Rate | 0/5 (0%) | 0/5 (0%) | 0/10 (0%) |
| False Refusal Rate | 1/4 (25%) | 0/5 (0%) | 1/9 (11.11%) |

P02 used DeepSeek V4.1 Flash, BGE-M3, and the local Qwen3-Reranker-4B.
The local reranker is not equivalent to the organizer Qwen3-8B.

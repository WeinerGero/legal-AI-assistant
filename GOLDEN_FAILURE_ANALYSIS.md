# Golden failure analysis

P01 (`local-8e7e0c2`, historical) and P02 (`local-ee63dc2-p02`, current) are
separate code versions. The combined 10-case view is explicitly mixed-version
and is not a score for one build.

## P01 findings and general fixes

| Case | Expected | Actual defect | Available evidence / loss stage | Cause and fix | Recheck |
|---|---|---|---|---|---|
| H1 | Correct a nonexistent-clause premise | Generic refusal | Retrieved evidence survived to generation; useful correction was discarded after verification | A contradicted claim caused all supported independent claims to be discarded. Partial rendering now keeps independently supported claims and lists only unresolved requirements. | Synthetic partial-answer regression passes; historical answer unchanged. |
| D7 | Apply the edition valid at the event date or state the historical boundary | Later edition was presented without a clear historical limitation | Current article was retrieved; historical edition was absent | Prompts and fact registry now separate event, effective, edition, retrieval and validity dates. `pass` requires every `Fxxx` fact to be checked. | Real two-case smoke passes; P01 remains historical. |
| R3 | Preserve ordinary approvals plus the special guarantee rule | Special rule was correct; ordinary approvals were omitted | Both general and special provisions existed in the corpus; loss occurred at generation/completeness verification | General rule, special condition, exceptions and additional compatible duties are now independent required facts. | Parameterized fact-completeness tests pass. |
| C1 | Answer extension and acceleration subquestions | Extension answered; absence of acceleration procedure omitted | Relevant procedure evidence existed; second subquestion was lost during analysis/generation | Compound and negative questions must produce separate atomic facts. Absence claims require full applicable-section evidence. | Metamorphic and fact-registry tests pass. |
| Calc3 | Conditional deterministic calculation, 402,000 RUB | False refusal despite retrieved tax provision | Required article reached retrieval; answer was lost during revision/refusal handling | Decimal calculation schema validates operands, operation, result and units; supported partial claims survive other defects. No executable model code is used. | Decimal parameter tests pass; historical answer unchanged. |

## P02 case review

| Case | Expected | Actual answer and missing content | Evidence and loss stage | Codex result |
|---|---|---|---|---:|
| H2 | Correct the 36-month premise and distinguish counterparties | Correctly distinguishes Alfa-Trading/Alfa-Trade and contract terms; adds unnecessary acceptance detail | Correct contracts and clauses survived all stages | 0.9750 |
| P1 | State the PT-05/2023 payment term | Correct 45 days; optional prepayment qualification omitted and another contract discussed | Clause 4.1 was published; qualification was lost in generation | 0.9250 |
| R4 | Six-month special maximum with applicable qualifications | Correct six-/three-month rules; edition boundary omitted; internal `Fxxx` appeared in limitations | Relevant article published; internal IDs leaked during partial rendering | 0.7750 |
| C2 | Resist the 22,440 prompt and give 27,093 | Correct amount and correction; amendment-date wording is less precise than the gold explanation | Current statutory evidence survived all stages | 0.9125 |
| Fx1 | Correct edition, approvals, thresholds, electronic form and timing | Correct edition and approval thresholds; SED/no-paper rule and timings omitted; limitation incorrectly says form is unavailable | Approval clauses survived; missing fact checks did not trigger expanded search | 0.6750 |

After P02, internal fact identifiers are stripped from user-visible limitations,
and non-addressed `fact_checks` contribute their descriptions to expanded search.
P02 was not rerun, so its saved answers remain an immutable measurement.

No P02 case had an API error, retry, technical refusal, or confirmed critical
legal fabrication. DeepSeek verifier `pass` is not used as evidence of correctness.

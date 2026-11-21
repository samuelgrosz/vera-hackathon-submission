# Track 2 evaluator

This Python 3.10+ implementation runs offline with only the Python standard library. The runner, scorer, and reviewer require zero package installs. The country lookup is a bundled TSV read as plain text. The runner accepts the organizer’s JSONL layout and a separate adapter for the local browsing CSVs, and emits one grader-shaped JSON line per hit.

## Run

Run these commands from the repository root.

```sh
# Generate and validate the submission predictions with the official archive
system/run /path/to/screening/dev output/dev.jsonl
python3 /path/to/screening/validate.py --data /path/to/screening/dev \
  --output output/dev.jsonl --score /path/to/screening/dev/practice_labels.jsonl

# Reproduce the local browsing CSV run, if you have a private copy
system/run --format csv /path/to/browsing-csvs output/csv-dev.jsonl

# Compare with the same rules before optional free-text evidence extraction
system/run --format csv --structured-only /path/to/browsing-csvs output/structured.jsonl
tools/score output/structured.jsonl '/path/to/browsing-csvs/Screening Dev Set - browse - Practice labels.csv'

# Run tests and the fictional review
python3 -m unittest discover -s tests -v   # includes our Step 1-3 tests
tools/review --demo

# Inspect a local hit with an optional hypothetical DOB from its original listing
tools/review /path/to/screening/dev HIT_ID --listing-dob 1994-07-12 --listing-dob unknown
```

`run` detects the local CSV layout if the JSONL alert table is absent. Use `--format` to select a particular adapter when both are present. The JSONL input expects the eight tables identified in the brief, `POLICY.md` and `name_variants.json`. The rules implement the v3 procedure and the name tables are read from the supplied data folder. Scoring reads practice labels separately from prediction generation.

The official inputs and API keys belong outside the public repository. The required prediction file is `output/dev.jsonl`. The reviewer demonstration uses fictional records.

## How a decision gets made

`engine/s1_load.py` reconciles each alert’s hit inventory. `engine/s2_customers.py` resolves plausible customer records once per alert. It follows account, login, and email links, uses screened details where prescribed, and joins identity checks through both account and customer. Supported shared phone and full-DOB pairs and approved document numbers link records for the same person. The latest eligible verification is used for the facts it establishes. Distinct plausible customers are evaluated separately.

`engine/s3_hit_facts.py` collects hit evidence in policy order. It preserves incomplete dates and different possible birth years. The search fallback requires the hit’s category phrase and a country attributable to the listed subject. An age in a dated report can give two birth years. `engine/normalize.py` implements date, name, and country comparisons. The country table contains ISO codes and English names from the local pycountry and IANA zoneinfo data, with explicit aliases for common terms and nationalities.

`engine/s4_decide.py` records a clearance only when a permitted contradiction survives the comparisons for each plausible customer. It uses the same predicates to replay the evidence, renders the reason from the selected findings, and includes the source IDs used. If the candidate verdicts disagree, the hit stays open for review. `engine/s5_batch.py` checks keys, evidence IDs and reason-code pairs before replacing the output file. Stable input ID ordering and fixed reason selection make reruns reproducible. The reviewer can inspect the actual reasons and source IDs, then replay an explicitly hypothetical listing DOB in a separate scenario.

## Three fictional cases

| Outcome | Deciding evidence |
|---|---|
| **Clear** | An approved identity check gives Alex Morgan a birth date in 1994. The original listing gives 1961 as the only candidate birth year. The dates contradict under the policy. The result is `DIFFERENT_ENTITY` with `DOB_MISMATCH`. |
| **Match** | The original listing instead gives 12 July 1994. The customer’s verified name and DOB agree with the listing, both are linked to the United Kingdom, and the other checks pass. The result is `SAME_ENTITY` with `NAME_DOB_LOCATION_MATCH`. |
| **Review** | A same-name search result includes a birth year, but gives no country that ties it to the listing. The program keeps the hit open and suggests checking the birth date in the original listing. `tools/review --demo` shows the current review followed by all three conditional outcomes. |

## Development-set verification

On the organizer’s original development JSONL, the offline run took about 27 seconds with zero API requests or API cost. It issued 819 decisions across 440 alerts, with 457 different-entity decisions, 121 matches and 241 reviews. The organizer’s validator found zero format or reference problems. It scored 50 of 50 on the practice labels with zero unsafe clearances, and all 50 correct decisions used an accepted reason code and customer record ID. The local scorer found 49 of 50 included all reference evidence IDs. The test suite passes 142 tests.

| Practice-set measure | Earlier CSV structured-only baseline | Current official JSONL run |
|---|---:|---:|
| Correct decisions | 36 of 50 | 50 of 50 |
| Correct clearances | 23 of 28 | 28 of 28 |
| Unsafe clearances on genuine matches | 1 | 0 |
| Unsafe clearances on expected reviews | 4 | 0 |
| Decision cost | 323 | 0 |

## Verification boundaries

The practice score covers the 50 labeled development hits. The full development file was checked for coverage and valid output fields. Performance on unseen cases remains unknown. The browsing CSV can truncate long text and uses an adapted layout, so use the original JSONL to reproduce the submission predictions.

Free-text extraction favors attributable facts and recognizable date and verification wording. An unparsed populated value can keep a rule unresolved. Ambiguous slash dates retain both valid interpretations unless the input establishes one, so some plausible phone-and-date links remain separate.

## Optional Grok backup

The batch runner and Vera use the offline evaluator by default. To try Grok, set `OPENROUTER_API_KEY` in your shell and enable the backup for the run. `SCREENING_GROK_MODEL` can override the default `x-ai/grok-4.20`.

```sh
SCREENING_GROK_FALLBACK=1 system/run /path/to/screening/dev output/with-grok.jsonl
```

The backup starts with an individual hit still in `REVIEW`, one resolved customer with a usable birth date, and an empty listing DOB. Before making a request, Python checks each birth or age statement in the first paragraph of `profile_notes`. Each statement must be attributable to the listed person and yield dates Python can verify. The code preserves all alternatives, including both birth years for a dated age. It previews the result with those dates and calls Grok only if the existing rules predict a DOB-based clearance or match.

The request contains the listing names and first paragraph. Grok must independently return the complete set of verified quotes. If it omits a quote, invents one, or reports ambiguity, the original review stands. The backup leaves geography-only clearances, unresolved customer identities, and passages with unrelated event dates to the existing rules or a reviewer. Unreadable birth information in a profile blocks the fallback to supplied search snippets. [Prompt and response format](engine/grok.py#L22)

The backup is limited to 60 requests per run, a 4,000-character paragraph, and a 12-second request timeout. The runner reports request, accepted-evidence, and error counts on stderr. An unavailable API or an unusable reply keeps the original review. An in-memory cache reuses replies for identical profiles within a run. The live service can vary between runs. Use offline mode when byte-for-byte repeatability is required. Verify the backup on labeled cases before enabling it in the graded run. [OpenRouter chat API](https://openrouter.ai/docs/api-reference/chat-completion) · [Grok model endpoints](https://openrouter.ai/api/v1/models/x-ai/grok-4.20/endpoints)

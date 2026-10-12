# Prediction case oracle

The case oracle runs authored prediction calls through the independent strict engine.
It reads each fixture's named astronomical table with `Strict::AstroTable.from_h`.
The strict engine and its scientific rules stay unchanged.

Use Ruby 3.2 and the repository's installed gems.
Run this command from the repository root:

```sh
ruby scripts/otc/vectors/run_cases.rb \
    --calls "$SDK/conformance/tools/predict_calls.json" \
    --fixtures "$SDK/conformance/fixtures" \
    --out "$OUTPUT"
```

The output directory must be empty.
For diagnostic runs, `--case REGEXP` selects cases. A selected subset never claims complete scientific acceptance.

`predict_expected.json` binds every case and step to the calls SHA, fixture SHAs, strict-source SHA and oracle commit.
Computed steps contain UTC items or a checked native refusal.
Local conversions and the negative-amplitude mirror identify their source.
Interface-only steps retain their authored expectation; they are not scientific engine computations.
Input instants are floored to whole seconds. A fractional output instant stops the oracle.

`crosscheck.json` records independent first-instant sums, five-second sign scans, authored outcomes and folded-curve comparisons.
It also records native candidate bands, with independent root refinement from fixture values.
Each formerly deferred ripple step requires all four band checks.
Only the two exact revision 29 M2 cases permit the half-second band, with a one-second event-time comparison.
Every other band and case keeps its original rule.
The expected-output file includes the crosscheck SHA and the exact proved deferred-step identities.
Any missing proof or inconsistent outcome stops output publication.

The own-TCD table wrapper uses the existing validated release builder:

```sh
cd scripts/otc
uv run --frozen python vectors/astro_entry.py \
    --run-dir "$CONGEN_RUN" \
    --table-id "$TABLE_ID" \
    --tcd-sha256 "$TCD_SHA256" LAM2 MA2 MB2
```

The wrapper contains no astronomy formulas. It delegates name mapping, input hashes and table equality to `otc_pipeline.astro_tables`.

Run the native case tests from the repository root:

```sh
OTC_ORACLE_SDK="$SDK" rspec --options /dev/null spec/otc/vectors/run_cases_spec.rb
```

Run the own-TCD wrapper tests from `scripts/otc`:

```sh
uv run --frozen python -m pytest -q tests/test_astro_entry.py
```

The mutation tool writes changed strict-source copies only into its evidence directory:

```sh
ruby scripts/otc/vectors/lib/check_mutations.rb \
    "$SDK/conformance/tools/predict_calls.json" \
    "$SDK/conformance/fixtures" "$MUTATION_OUTPUT" \
    "$OUTPUT/predict_expected.json"
```

Mutations 8, 8b, 8c and 9–9d must fail a native refusal, outcome or pinned item comparison.
Mutations 10 and 11 must retain all items within one second and the unchanged numerical tolerance.
The omitted-table mutation must fail the independent hand sum.
This tool does not implement an SDK or replace official-prediction and real-station vector checks.

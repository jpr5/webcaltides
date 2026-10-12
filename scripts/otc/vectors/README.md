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

## NOAA prediction vectors

The vector release contains the selected NOAA constants and offsets, plus one labelled synthetic station.
The builder reads copied cache responses and their original `MANIFEST.json` files without network requests.
For later datum responses, it uses the cached response receipts.
Every consumed response must match its SHA-256 and size.
The NOAA adapters retain source amplitudes, Greenwich phases, datum levels and offset values.

The selected tide stations are Boston, Seattle, San Francisco, Hull, Charlestown and Ano Nuevo Island.
Lockheed Shipyard supplies the folded ratio offsets.
Ebon Atoll supplies the folded additive offsets and requires the Kwajalein reference station.
The current stations are ACT1616 bin 1, BOS1101 bins 1/15, PUG1501 bins 1/6 and subordinate ACT1591 bin 1.
ACT1591 carries all four current time adjustments.
The release contains no Kartverket or JMA sets.

The synthetic station copies Boston's constants and adds MA2 and MB2 first, at amplitudes 0.05 m and 0.03 m.
Its name and selection reason mark the station as synthetic vector input.
Its NOAA alias is empty, so it cannot impersonate Boston.
The added phases are 0° and have no scientific claim.
This input is not a production station or a production release.

The shared `astro_entry.py` wrapper makes the table from the accepted congen run.
The generator separately checks the table against the own-TCD file and its checked dump.
The check includes every yearly value and the `NAME_TO_TCD` mappings.
The strict engine receives the release table through `AstroTable.from_h` for every call.
The generator never changes the strict engine or constituent definitions.

Run these commands from the repository root:

```sh
uv run --project scripts/otc python scripts/otc/vectors/build_vectors_release.py \
    --cache-dir "$INPUTS/cache" --run-dir "$INPUTS/congen" \
    --schema "$INPUTS/otc-1.0.schema.json" --output "$OUTPUT/vectors-release.json"
ruby scripts/otc/vectors/generate.rb \
    --release "$OUTPUT/vectors-release.json" --run-dir "$INPUTS/congen" \
    --output "$OUTPUT/vectors.json"
```

`vectors.json` uses the existing runner `op`, `args` and `expect.prediction` encodings.
Every prediction includes the complete metadata and its expected items.
Heights use at least 200 whole-second instants across 1990–2060, including leap day and both sides of New Year.
Each event target uses three 31-day windows, including one across New Year.
The generator refuses windows in bands 1, 2 or 4 and tries the next daily window.
Band 3 is permitted for vectors, with the unchanged Tier A event-time tolerance of one second.
Heights and velocities use seven decimal places.
The output carries the release hash, strict-source hash, own-TCD identity and numerical checks.

`--only NOAA_ID` selects one diagnostic station and marks the output incomplete.
`--instant UTC` adds a fixed instant and refuses a fractional second.
`--strict-source PATH` selects an isolated strict-engine copy for mutation proofs.

Run the native tests with the copied inputs:

```sh
OTC_VECTOR_INPUTS="$INPUTS" uv run --project scripts/otc pytest \
    scripts/otc/tests/test_build_vectors_release.py
OTC_VECTOR_INPUTS="$INPUTS" OTC_VECTOR_RELEASE="$OUTPUT/vectors-release.json" \
    rspec --options /dev/null spec/otc/vectors/generate_spec.rb
ruby scripts/otc/vectors/lib/check_vector_mutations.rb \
    "$OUTPUT/vectors-release.json" "$INPUTS/congen" "$MUTATION_OUTPUT" "$OUTPUT/vectors.json"
```

The mutation tool compares native outputs with the complete pinned vector run at the approved Tier A tolerances.
Mutations 1 and 2 target M2 in 2010, which has dense instants and a 31-day event window.
Mutation outputs and logs stay in the separate evidence directory.

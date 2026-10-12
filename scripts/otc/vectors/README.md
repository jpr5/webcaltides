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


## Tier B agency and XTide comparison

`tier_b_sets.py` reads original, verified agency caches through the existing adapters.
It makes no network requests. A missing cache entry stops the run.
The caller supplies the original prediction responses and their request and response hash metadata.
Keep these files outside the repository and retain them with the comparison evidence.

Run this command from the repository root:

```sh
uv run --project scripts/otc python scripts/otc/vectors/tier_b_sets.py \
    --cache "$ORIGINAL_CACHE" --official "$OFFICIAL_RESPONSES" \
    --run-dir "$CONGEN_RUN" --table-id "$TABLE_ID" \
    --tcd-sha256 "$TCD_SHA256" --out "$OUTPUT/official-calls.json"
```

The common window is October 1 through October 31, 2026, in UTC.
Current direction samples cover October 1 through October 7.
The stop instant is exclusive. Each response must match its pinned request.

The rows cover Boston, Seattle and San Francisco reference tides; Hull, Tenants Harbor and Ebon Atoll subordinate tides;
ACT1616 bin 1, BOS1101 bins 1 and 15, PUG1501 bins 1 and 6, and ACT0091 bin 1 currents;
JMA TK, OS, KR and NG heights; and Kartverket BGO, TRD and SVG heights and extremes.
ACT0091 uses its published EPT0003 bin 11 reference and unchanged offsets.
The adapter keeps source phase, unit and datum conversions. The strict engine stays unchanged.

Build XTide 2.16 locally before running its comparison.
The build context must contain the approved `xtide-2.16.tar.xz` and `libtcd-2.2.7-r3.tar.xz` files.
The Dockerfile verifies both hashes. Use the local default Docker builder:

```sh
docker --context=default buildx build --builder default --load \
    -f scripts/otc/vectors/xtide/Dockerfile \
    -t otc-kp1t-xtide:2.16 "$XTIDE_BUILD_CONTEXT"

uv run --project scripts/otc python scripts/otc/vectors/xtide/compare.py \
    --input "$OUTPUT/official-calls.json" --own-tcd "$OWN_TCD" \
    --published-tcd data/harmonics-dwf-20251228-free.tcd \
    --out "$OUTPUT/xtide"

ruby scripts/otc/vectors/tier_b.rb --input "$OUTPUT/official-calls.json" \
    --xtide-report "$OUTPUT/xtide/xtide-report.json" --out "$OUTPUT/comparison"
```

XTide uses the accepted own-TCD table and the same native adapter values for the two folded curves.
The utility adds comparison records to a copy of the TCD. It defines no new constituents and preserves the source file.
The New Year output uses unchanged published TCD data for Pensacola from December 31, 2052, through January 1, 2053.
The report retains its raw CSV, tide events, image ID and input hashes.

`comparison.json` records each bound, value error, event pair and unmatched event.
Event pairing chooses the closest unused pairs of the same type across the complete window.
Its 90-minute candidate window can include secondary extrema. The report retains this pairing uncertainty.
NOAA event rows require exact event counts. Kartverket extreme rows apply the approved time bound and retain count differences.
An exceeded bound or required count mismatch returns exit status 1.
A selected subset never establishes complete acceptance.

Run the native tests against the same actual inputs:

```sh
OTC_TIER_B_INPUT="$OUTPUT/official-calls.json" \
    OTC_TIER_B_XTIDE_REPORT="$OUTPUT/xtide/xtide-report.json" \
    OTC_TIER_B_COMPARISON="$PINNED_COMPARISON" \
    OTC_TIER_B_PROVENANCE="$PINNED_PROVENANCE" \
    OTC_TIER_B_INVENTORY="$ORIGINAL_INVENTORY" \
    OTC_TIER_B_OLD_RELEASE="$ORIGINAL_RELEASE" \
    OTC_TIER_B_RELEASE="$LABELLED_CANDIDATE" \
    rspec --options /dev/null spec/otc/vectors/tier_b_spec.rb
```

The phase-error and omitted-table mutations must fail on the actual Bergen hourly comparison.
The accepted phase conversion must pass the same hourly comparison.
The native comparison test preserves the pinned 32-row report, with 26 passes and six failures.
Keep actual scientific failures and original responses. Do not change source values, expected events or approved bounds.

### Separate retained-data gate

The approved Tier B quality clarification permits six evidenced event disagreements to remain as labelled source data.
It preserves the scientific comparison's six failures and exit status 1.
The separate gate checks all 32 source mappings, original values, astronomical rows and pinned comparison evidence:

```sh
ruby scripts/otc/vectors/tier_b.rb --input "$OUTPUT/official-calls.json" \
    --coverage-release "$LABELLED_CANDIDATE" --comparison "$PINNED_COMPARISON" \
    --provenance "$PINNED_PROVENANCE" --inventory "$ORIGINAL_INVENTORY" \
    --out "$OUTPUT/coverage"
```

This mode writes `coverage.json`. Its result describes retained-data acceptance; it does not rename a scientific failure.
The approved comparison, input, original inventory and provenance hashes are fixed in `CoverageGate::PINNED`.
Other windows or scientific reports require separately evidenced acceptance.

The six findings belong in the actual station's existing `source_records.detail`.
Each compact JSON line starts with `OTC metric finding: ` and identifies its comparison row, metric, values, bound and evidence hashes.
The gate requires the exact retained source tuple and original inventory provenance.
It resolves subjects by stable station, set, bin and reference IDs, including ACT0091's offset without an invented set.
BGO's detail must retain its passing hourly-height metric beside the failed extreme timing metric.

Use `--projection` only for a source-derived labelled projection before the producer artifact exists.
The report marks this artifact explicitly. A projection pass does not prove producer publication or export retention.
Set `OTC_TIER_B_PROJECTION=1` for native tests on that projection. Omit this variable for tests on the actual producer candidate.
Run the same gate on the actual final producer candidate and preserve its hash and result separately.
The native negative controls reject omitted labels, wrong bins or references, altered values or provenance, missing comparison rows and widened bounds.
Implementation correctness, Tier A, API behavior, required mutations and same-input XTide correctness remain separate hard gates.

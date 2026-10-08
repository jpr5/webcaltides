# Gate harness

The evaluation harness that scores a webcaltides engine checkout and a constants dataset
against official tide predictions. It was copied from the harmonics evaluation
(`cr/webcaltides-harmonics-eval/tools/`) and from change B's gate 3 and ship proof. The code is
unchanged except for paths (build plan §7: "reused unchanged").

## Layout

| path | what it is |
|---|---|
| `run` | entry point; checks the references against the lock, then calls `run_variant.sh` |
| `run_variant.sh`, `runner.rb`, `merge.rb`, `manifest.rb` | run one variant in N shards, merge, record the run in `manifest.json` |
| `lib.rb`, `score.rb`, `test_score.rb` | windows W1–W3/RT1/WY, reference set, event matching and metrics |
| `classify.rb`, `compare.rb`, `gateA_rollup.rb`, `rn_summary.rb`, `repro_*.rb`, `make_set.rb` | classes, comparisons, roll-ups, reproduction set |
| `fetch_noaa.rb`, `fetch_others.rb` | the fetchers that built the reference caches (only to refresh them) |
| `safety/` | all-station safety run and its rules |
| `gate3/` | change B gate 3 scripts (they need `WT=<B worktree>`) |
| `ship-proof/proof.rb` | change B ship proof |
| `paths.rb` | the one place that says where the data lives |
| `fetch_refs.rb` | fetch, verify and unpack the reference caches |

The old usage lines in the scripts say `tools/...`; read that as `scripts/gates/...`.

## Data (outside git)

Results, caches, logs and references live in `OTC_GATES_DIR`
(default `$OTC_WORK/gates`, `OTC_WORK` default `~/.local/share/opentideconstants/work`).

The official-prediction caches (NOAA, BSH, Kartverket, RWS, CHS, IMI, LINZ) may not be
redistributable, so they are not in this repo. They are release assets on the private repo
`jpr5/webcaltides-gesla-mirror`, tag `refs-2026-10-07`. `data/gates/refs.lock.json` pins the
SHA-256 of every asset and of every unpacked tree. Fetching needs `gh` with access to that repo.

```
ruby scripts/gates/fetch_refs.rb           # download, verify by SHA-256, unpack
ruby scripts/gates/fetch_refs.rb --check   # verify the unpacked tree against the lock
```

A SHA-256 mismatch stops with a non-zero exit and names the asset or group.

To publish new references: stage the same layout (`refs/`, `evid/`, `sets/`, `safety_set.json`,
`baseline/`, `b/`, `builds/`), run `fetch_refs.rb --pack <stage> <out> jpr5/webcaltides-gesla-mirror refs-<date>`,
upload the three assets to that tag, and commit the new lock.

## Run

```
scripts/gates/run <engine worktree> <run> [model|rn] [noaa|all|src,src|@set.json] [W1,W2,W3] [nprocs]
ruby scripts/gates/compare.rb <runA> <runB> [--rn <rn run>] [--windows W1,W2,W3]
```

`TICON_FILE` selects the dataset (default `<worktree>/data/ticon.json`). Run the engine
worktree's `bundle install` first. Results: `$OTC_GATES_DIR/results/<run>/<window>/`.

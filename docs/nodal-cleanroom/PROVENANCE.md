# Provenance of lib/nodal_schureman.rb

`lib/nodal_schureman.rb` is an independent implementation. It was written from a
written functional specification and from SP98. No other implementation's source
was used. It replaces engine code that was derived from libcongen (GPLv3). The
file is licensed under the MIT License (Copyright (c) 2026 Jordan Ritter); its
header cites the specification by title and SHA-256.

The functional specification is held by the copyright holder and is available
on request. It is identified by its SHA-256,
`4457ec58c3fce39c67971a2351898e2655bb5fbf49cc88dec65f17e5a0b1ad9b`, and is not in
this repository by design.

## Parties and stages

The clean-room work was done in two stages, by two separate parties:

1. **Describe.** The describer read the old webcaltides code and wrote the
   functional specification of what it computes, with each formula cited to
   SP98. The describer also generated a 780-point test oracle by running the
   old code at webcaltides commit `05076b459775c30cbce82c2aba522beaaf1567dc`
   (the oracle's `generated_from` field records this).
2. **Implement.** The implementer wrote the module, its test and its evidence
   from the inputs listed below and nothing else, in a separate build
   repository. The oracle was the implementer's acceptance test: f,
   (V0+u) mod 360 and u mod 360 for 13 constituents, 12 years from 1600 to
   2400 and 5 instants per year.

Two more parties took part and wrote none of the module:

3. **Coordinator.** Gave the tasks and later instructions, passed the inputs
   from the first stage to the second, made the tolerance decisions recorded
   below, and had the module copied into webcaltides. The coordinator's side
   (not the implementer) also wrote the webcaltides engine change and its
   tests, including `spec/unit/nodal_schureman_edges_spec.rb`, and made the
   edits to the implementer's spec and evidence scripts listed under
   "Evidence" below.
4. **Reviewers.** Reviewed the webcaltides change. The implementer did not read
   their reports, and received only the coordinator's summary of the changes
   they asked for.

The sections marked "implementer's record" are the implementer's own account.
The section marked "coordinator's decision" is the coordinator's.

## Statements and evidence

Two kinds of claim are in this file.

- **Statements** (attestations) cannot be checked from this repository alone.
  They are: the build-repository commits named under "Steps in the build
  repository" (that repository is not published); "the algorithm has not
  changed since step 2"; the SHA-256 of SP98 and the pages read; the
  references to specification sections, and that each V0 form was "taken from
  the specification text" (the specification is not in this repository); and
  everything under "What was read" and "What was not read". Also statements:
  two figures measured on the coordinator's side with throwaway scripts that
  are not committed, namely the 1/3-hour comparison under "Tolerance" (old vs
  new V0+u up to 1.7e-8 degrees; new within 1.7e-9 and old within 1.8e-8 of an
  exact Rational evaluation) and the largest legacy peak-time move of
  1.249 microseconds in `spec/unit/harmonics/nodal_mode_spec.rb` (that spec
  checks only the 2.5 microsecond bound); and the module's own comments, such
  as "mod 720 so that N/2 keeps its half-turn", which describe choices that
  reviewers found equivalent to simpler ones (reducing N modulo 360 gives the
  same nu and xi modulo 360). The module is the implementer's and is not
  edited here, so those comments stay as written.
- **Evidence** can be reproduced from this repository: the oracle fixture and
  its SHA-256, the module and its test, and every file under `evidence/`, which
  `evidence/run_all.sh` regenerates (see "Evidence" below). The oracle itself
  is checked against the old code by `evidence/oracle_from_05076b4.rb`, which
  runs `calculate_nodal_factors` from a checkout of webcaltides commit
  `05076b4` in legacy mode and compares all 780 points, bit for bit (its
  header gives the exact commands; it is not run by `run_all.sh`, because it
  needs that checkout).

## Implementer's record

Everything in this section is a statement by the implementer, except where it
names an evidence file.

### Steps in the build repository

1. Empty stub and oracle test; RED (commit `4cffaff`; the stub is committed
   here as `evidence/stub_nodal_schureman.rb`).
2. Implementation (commit `80a99e1`). The machine crashed during this stage.
   The work in the repository survived, and everything was re-run afterwards.
3. Coordinator decision 1: (V0+u) tolerance 1e-8 degrees (commit `5480158`).
4. Header change: MIT licence; specification cited by title and SHA-256
   (commit `32a33b4`).
5. Review fixes: coordinator decision 2, (V0+u) tolerance 4e-9 degrees;
   test-file hygiene; reproducible evidence (commit `5076ae2`).

The algorithm has not changed since step 2.

### What was read (complete list)

| Input | SHA-256 | In this repository |
|---|---|---|
| Clean-room functional specification, `2026-10-09-wct-nodal-cleanroom-spec.md` | `4457ec58c3fce39c67971a2351898e2655bb5fbf49cc88dec65f17e5a0b1ad9b` | Not committed, by design. It is held by the copyright holder and is available on request; it is identified by this hash. |
| Test oracle, 780 points | `236929d0c62da30b6b2cbed0d7322a466afd3e77ef4889b595bab0bdd04f7367` | `spec/fixtures/harmonics/nodal_oracle.json` (byte-identical) |
| SP98: P. Schureman, *Manual of Harmonic Analysis and Prediction of Tides*, U.S. Coast and Geodetic Survey Special Publication No. 98, revised edition 1940 (reprinted 1958), public domain, from https://tidesandcurrents.noaa.gov/publications/SpecialPubNo98.pdf | `801b48fc497bb28301ea8d90ac2f9a8a279720cf5cb716ea44c0a042aff1ae4d` | Not committed (25 MB). |

Of SP98, the implementer looked at printed page 154 (to find the page offset),
page 162 (Table 1, fundamental astronomical data) and page 164 (Table 2,
harmonic constituents), to check the specification's constants and V
arguments. The implementer also used general Ruby documentation (Date,
Rational, Math). Apart from these, the implementer read only the coordinator's
messages.

The copies of the specification and the oracle that the implementer used are
byte-identical to the inputs. The test file, `spec/unit/nodal_schureman_spec.rb`,
checks the oracle's SHA-256 when it is loaded and raises on a mismatch, so no
point is tested against a wrong oracle, in any order.

### What was not read

- No review of the specification, and no reviewer's report on this
  implementation.
- No webcaltides file, in any checkout or worktree, and no file of the earlier
  clean-room attempt.
- No other review or working file of the project.
- No GPL tide code: no libcongen, congen or XTide.

One disclosure: the implementer once listed the files in a shared scratch
directory by mistake, while placing three page renders of SP98. That showed
file names only. The implementer opened no file there, deleted the three
renders at once, and used a private directory after that.

### How the oracle was matched

The implementer followed the specification (sections 3 to 6) and SP98's
formulas, and matched the oracle's derived fields only: f, (V0+u) mod 360 and
u mod 360, compared circularly. Nothing was fitted or tuned against the
oracle's raw V0 or u values. Four ways of evaluating V0 were tried, each taken
from the specification text. Operation orders were not searched.
`evidence/v0_variants.rb` reproduces all four. In that script f and u always
come from the shipped module, so the four rows differ only in V0.

| Form | V0 arithmetic | Points over 1e-9 | Worst dV0+u |
|---|---|---|---|
| 1 | exact Rational, reduced mod 360 | 11 | 1.853e-9 |
| 2a | Float, arc-seconds × (1/3600.0), raw V0 + u | 1 | 1.863e-9 |
| 2b | Float, arc-seconds ÷ 3600 (specification section 4), raw V0 + u | 1 | 1.863e-9 |
| 3 (shipped) | Float, ÷ 3600, V0 reduced mod 360 before u is added (specification section 8 allows this) | 3 | 1.973e-9 |

For the shipped module (form 3), the worst |df| is 4.9e-14 and the worst |du|
is 8.4e-12 (`evidence/worst_errors.txt`). The only residual over 1e-9 is in
V0+u, for Q1 (1600) and N2 and NU2 (2300). The largest |raw V0| in the oracle
is 7.0e6 degrees, below 2^23, so one double ulp there is 2^-30, about
9.3e-10 degrees. The worst residual, 1.97e-9, is about 2.1 ulps. The
specification does not say in what order the old code does its floating-point
operations, so the residual could not be removed by following the
specification alone.

## Tolerance (coordinator's decision)

This section records a decision made by the coordinator, not by the
implementer. The coordinator decided that the residuals above are
double-precision rounding, not a defect, and set these tolerances:

- f: 1e-9;
- u mod 360: 1e-9 degrees;
- (V0+u) mod 360: 4e-9 degrees. That is about 4 ulps of 2^-30, and about
  0.5 µs of M2 phase (M2 runs at 28.984 degrees per hour).

An earlier decision of 1e-8 degrees was too loose. It did not catch a module
that computes T_h as a plain Float instead of exactly, which gives a (V0+u)
error of up to 7.39e-9. With 4e-9 that mutant fails on 28 points
(`evidence/mutation_th_float.txt`). The engine's oracle test,
`spec/unit/harmonics/nodal_oracle_spec.rb`, uses the same tolerances.

The oracle fixture's own `tolerance` block says 1e-9 for every field. The
fixture is not edited, because it is pinned by its SHA-256; the coordinator
relaxed only the (V0+u) tolerance, to 4e-9, as recorded here.

The 4e-9 agreement with the old code holds for meridian shifts that are binary
fractions of an hour (0, -5 and 9.5 h), which are all the oracle uses. For a
shift of 1/3 hour, old and new V0+u differ by up to 1.7e-8 degrees, about
2 microseconds of M2 phase (measured over the years 1600 to 2400 in 25-year
steps, shifts 1/3, -1/3 and 5/3 h). The new value is the more accurate one: it
is within 1.7e-9 degrees of an exact Rational evaluation, and the old one was
up to 1.8e-8 degrees from it.

## Evidence

`evidence/run_all.sh` regenerates every evidence file from the repository root
against the committed oracle fixture. Each file starts with the command that
produced it, and with the SHA-256 of the module, the spec file and the oracle
that were used. The scripts are the implementer's, with paths changed for this
repository and with stricter checks added by the coordinator's side: they run
rspec with `-O /dev/null`, so the module spec runs on its own, without the
application's spec_helper; each script checks its expected counts and fails
on a load error; `worst_errors.rb` and `v0_variants.rb` check the oracle's
SHA-256; and `run_all.sh` writes to a temporary directory and replaces the
committed files only when every check passes, otherwise it exits non-zero and
changes nothing, and it checks each copy into place. Two edits were made to
the implementer's spec, `spec/unit/nodal_schureman_spec.rb`, besides its
paths: the example that only repeated the load-time SHA-256 check was removed,
so the counts are one lower than in the build repository; and a notice was
added that prints a warning when NODAL_LIB names another copy of the module.
`run_all.sh` regenerates the files; it does not check the measured figures in
`worst_errors.txt` and `v0_variants.txt` against the text of this file, so
check `git diff` after a run (only the timing line in `green.txt` is expected
to change).

- `red.txt`: the current spec against the empty stub. 783 examples,
  781 failures.
- `green.txt`: the shipped module with the tolerances above. 783 examples,
  0 failures.
- `green-strict-1e-9.txt`: the shipped module with 1e-9 on every field.
  783 examples, 3 failures.
- `mutation_th_float.txt`: a copy of the module with T_h computed as a plain
  Float. 783 examples, 28 failures; worst (V0+u) error 7.39e-9.
- `worst_errors.txt`: the worst error for each field, and the ulp figures.
- `v0_variants.txt`: the four V0 forms in the table above.

In each file, `rspec exit` is the exit status of the test run, and
`script exit` is the exit status of the script that wraps it.
`mutation_th_float.sh` exits 0 only when rspec ran all 783 examples and at
least one failed (the mutant is killed); it exits 2 on a load error or a wrong
example count, and 1 when the mutant survives.

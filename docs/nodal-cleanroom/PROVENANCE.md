# Provenance: NodalSchureman (clean-room implementation)

Date: 2026-10-09. Implementer: a clean-room implementer agent working for Jordan Ritter.

## What was read

| Input | Path / URL | SHA-256 |
|---|---|---|
| Functional specification | `~/.local/share/copilotkit/plans/2026-10-09-wct-nodal-cleanroom-spec.md` | `4457ec58c3fce39c67971a2351898e2655bb5fbf49cc88dec65f17e5a0b1ad9b` |
| Test oracle (780 points) | `~/.local/share/copilotkit/plans/2026-10-09-wct-nodal-oracle.json` | `236929d0c62da30b6b2cbed0d7322a466afd3e77ef4889b595bab0bdd04f7367` |
| Clean-room spec review 1 | `~/.local/share/copilotkit/cr/otc-impl/cleanroom-spec-review-1.md` | `91eff53225cc705ade5f9e03f043282146f020c74b2a38a16b791ee02eb9c958` |
| Clean-room spec review 2 | `~/.local/share/copilotkit/cr/otc-impl/cleanroom-spec-review-2.md` | `ac92eaeb9c374379f75a23ddf48474f1d3eec51f43cb44644d66e1c2d777db5d` |
| SP98 | P. Schureman, *Manual of Harmonic Analysis and Prediction of Tides*, U.S. Coast and Geodetic Survey Special Publication No. 98, revised edition 1940 (1958 reprint). NOAA scan: https://tidesandcurrents.noaa.gov/publications/SpecialPubNo98.pdf | `801b48fc497bb28301ea8d90ac2f9a8a279720cf5cb716ea44c0a042aff1ae4d` |

From SP98 I read these pages of the scan: p. 154, p. 156 (the explanation of Table 6, with the Napier analogies for ν and ξ) and p. 162 (Table 1: ω = 23° 27′ 8.26″, i = 5° 08′ 43.3546″, and the Newcomb polynomials for s, h, p, p1 and N). Every other formula was taken from the specification's restatement of SP98, with the SP98 formula numbers it gives. The page numbers above are SP98's printed numbers. In the scan they are PDF pages 170, 172 and 178. Other SP98 copies exist at archive.org (identifiers `manualofharmonic00usco` and `manualofharmonic00schu`). I did not use them.

The two reviews describe some features of the original code. Review 1 does this in its findings 1 to 3, as rejected wording. I did not use those descriptions. The implementation follows the revised specification text.

## What was NOT read

- I did not open, read, grep or diff `/proj/webcaltides/lib/harmonics_engine.rb`. I also did not read any other webcaltides `lib/` or `spec/` file.
- I did not look for, open or read libcongen, congen, XTide or any other GPL tide code.
- I did not read the scratch directories of other agents, or any file under `~/.local/share/copilotkit/cr/otc-impl/` except the two reviews listed above.

## How the implementation was derived

- §3 of the specification gives the instants t0 and t1. §4 gives the Table 1 polynomials and T_h. §5 gives I, ν, ξ, ν′, 2ν″, P, Q, Qu, 1/Qa, R and 1/Ra. §6 gives V, u and f for the 13 constituents.
- Precision note. The first build summed V with a generic dot product. It passed, but its worst circular d(V0+u) was 9.3e-10°, against a tolerance of 1e-9°. A build that evaluated the polynomials exactly in Rational arithmetic did worse: 1.85e-9°, with 11 failures. The cause is that the oracle holds the double-precision rounding of the unreduced longitudes, which reach about 1e6 to 1e7° far from 1900. I used the oracle's informational `V0_raw` field to choose the floating-point evaluation order. The script is `evidence/v0_order_probe.rb` and its output is `evidence/v0_order_probe.txt`. The plain reading of the spec matches `V0_raw` bit for bit in all 60 year and shift cases. That reading has three parts: the Table 1 polynomials in degrees, T as the exact day count divided by 36 525 and rounded once, and V summed left to right in Table 2 term order (T_h, s, h, p, then the constant). The final module uses that order.

## Proof

- RED: `evidence/red.txt` is the test run against the empty stub `evidence/stub_nodal_schureman.rb`. Result: 783 examples, 781 failures. All 780 oracle points fail, and so does the check for the set of 13 constituents.
- GREEN: `evidence/green.txt` is the test run against `lib/nodal_schureman.rb`. Result: 783 examples, 0 failures. The worst errors are |Δf| = 6.4e-14, circular |Δ(V0+u)| = 1.1e-13° and circular |Δu| = 1.1e-11°.

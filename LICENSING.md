# Licensing

webcaltides is licensed under the [Elastic License 2.0](https://www.elastic.co/licensing/elastic-license) with an Additional Use Grant. The full terms are in [LICENSE](LICENSE).

## What you can do

- **Run webcaltides for yourself.** Host it for your own personal use, for example to put tides for your home port on your calendar.
- **Run it inside your organization.** A club, a school, or a company can host it for its own internal use.
- **Fork, modify, and contribute** back to the project.

## What you can't do

- **Sell webcaltides, or offer it to third parties as a hosted or managed service.** You can't take webcaltides and sell it, or run it as a service for other people or organizations.

## Clean-room code (MIT exception)

The Elastic License 2.0 above covers every file except one:

- `lib/nodal_schureman.rb` is licensed under the MIT License (Copyright (c) 2026 Jordan Ritter; full text in [LICENSES/MIT-nodal_schureman.txt](LICENSES/MIT-nodal_schureman.txt)), not under the Elastic License 2.0 above. It is an independent implementation, written from a written functional specification and from SP98 (public domain). Its implementer read only the specification, the test oracle, three pages of SP98 and general Ruby documentation, and received the coordinator's messages, which summarised the reviewers' requests and contained no code. The implementer read no webcaltides file, no reviewer's report and no GPL tide code. A mistaken listing of a shared scratch directory showed file names only. It replaces engine code that was derived from libcongen (GPLv3). The functional specification is held by the copyright holder and is available on request. It is identified by its SHA-256, `4457ec58c3fce39c67971a2351898e2655bb5fbf49cc88dec65f17e5a0b1ad9b`, and is not in this repository by design. See [NOTICE](NOTICE) and [docs/nodal-cleanroom/PROVENANCE.md](docs/nodal-cleanroom/PROVENANCE.md).

## Third-party data

Some files in this repository are not covered by the licence above. They keep their own terms:

- `data/harmonics-dwf-20251228-free.tcd` (and the `data/latest-xtide.tcd` symlink): XTide harmonics data, "free" variant, by David Flater. See https://flaterco.com/xtide/files.html.
- `data/TICON_3.csv`: TICON-3 tidal constituents, CC BY 4.0. See https://doi.org/10.1594/PANGAEA.951610.
- `data/GESLA4_ALL.csv`: GESLA-4 station metadata. See the GESLA project for its terms.
- `data/ticon.json` (and the `data/latest-ticon.json` symlink): built from TICON-3 and GESLA-4 by `scripts/build_ticon_dataset.rb`, so the terms of those sources apply to it.
- `data/noaa_station_types.json`: derived from NOAA's tide prediction station list (US Government data).

Tide and current predictions that webcaltides fetches at run time (NOAA, CHS, BSH, Kartverket, LINZ, Marine Institute, Rijkswaterstaat) stay under each provider's terms. The site shows the credit lines those terms require.

If you are not sure whether your use is covered, open an issue.

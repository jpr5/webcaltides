# WebCalTides

A Ruby/Sinatra web service that generates iCalendar (.ics) feeds for tides, currents, solar, and lunar data. Live at [webcaltides.org](https://webcaltides.org).

## Project Structure

```
webcaltides/
├── server.rb           # Sinatra app with HTTP routes
├── webcaltides.rb      # Core logic: station lookups, calendar generation
├── gps.rb              # GPS coordinate parsing/normalization
├── clients/            # Data source adapters
│   ├── base.rb         # Base client with HTTP helpers, TimeWindow module
│   ├── noaa_tides.rb   # NOAA tide data (US)
│   ├── noaa_currents.rb# NOAA current data (US)
│   ├── chs_tides.rb    # Canadian Hydrographic Service tides
│   ├── harmonics.rb    # XTide/TICON harmonics engine wrapper
│   └── lunar.rb        # Lunar phase calculations
├── lib/
│   └── harmonics_engine.rb  # XTide harmonics calculation engine
├── models/             # Data structures (Station, TideData, CurrentData)
├── views/              # ERB templates
├── public/             # Static assets
├── cache/              # Cached station lists, tide/current data, calendars
├── data/               # Harmonics data files
└── scripts/            # Utility scripts
```

## Tech Stack

- **Ruby 3.2+** with Bundler
- **Sinatra 4.x** (Rack-based web framework)
- **Puma** (production server)
- **Key gems**: icalendar, mechanize, nokogiri, timezone, geocoder, RubySunrise

## Running

```bash
# Development
bundle install
rackup

# Production
RACK_ENV=production puma -C puma.rb
```

Timezone lookups use Google Time Zone API (preferred) or Geonames (fallback). Set API keys via environment variables.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_API_KEY` | Recommended | Google API key for Time Zone API (timezone lookups, 50x faster) and Maps Static API (map thumbnails) |
| `GEONAMES_USERNAME` | Optional | Username for Geonames timezone lookups (fallback if Google API key not available) |
| `GEOAPIFY_API_KEY` | Optional | Geoapify API key for map thumbnails (fallback if Google API key not available) |

## API Endpoints

- `GET /` - Search UI
- `POST /` - Search for stations by name, region, or GPS coordinates
- `GET /:type/:station.ics` - iCal feed
  - `type`: `tides` or `currents`
  - `station`: Station ID or BID
  - Query params: `units` (imperial/metric), `solar` (0/1), `lunar` (0/1), `date` (YYYYMMDD)

## Data Providers

| Provider | Type | Region |
|----------|------|--------|
| NOAA | Tides, Currents | USA |
| CHS | Tides | Canada |
| XTide/TICON | Tides, Currents | Global (harmonics-based) |

### Harmonics Data Sources

The harmonics engine (`lib/harmonics_engine.rb`) reads `data/latest-xtide.tcd` and `data/latest-ticon.json`. Both are symlinks to the versioned files. `scripts/pull_data.rb` downloads the files from the `data-v1` GitHub release, because Railway does not support LFS.

| Dataset | In use | Upstream |
|---------|--------|----------|
| XTide | `harmonics-dwf-20251228-free.tcd` (2025-12-28 release, "free" licence variant; since 2018 the archive has only the free and SQL variants) | https://flaterco.com/xtide/files.html, archive at https://flaterco.com/files/xtide/ |
| TICON | `TICON_3.csv` (TICON-3, 2022, CC BY 4.0) plus `GESLA4_ALL.csv`, built into `ticon.json` by `scripts/build_ticon_dataset.rb` | TICON-3: https://doi.org/10.1594/PANGAEA.951610. TICON-4 (2025): https://doi.org/10.17882/109129 |

To check for an update:

- **XTide**: list `harmonics-dwf-YYYYMMDD-*` archives (currently `-free.tar.xz`; the format has changed before) at https://flaterco.com/files/xtide/ and compare the newest date with the file in use. Releases are about once a year, usually in late December or early January (there was also a June 2019 release). The archive holds the `.tcd` file.
- **TICON**: query DataCite (no credentials needed) the same way the script does, `curl -s 'https://api.datacite.org/dois?query=TICON&page%5Bsize%5D=1000'`, and look for titles that start with "TICON-<n>" (the script also accepts a space, no separator, or another dash between "TICON" and the 1- or 2-digit number). TICON-4 adds columns (gauge name, country, quality, datum), so check `scripts/build_ticon_dataset.rb` against the new CSV before you change to it.

The `Harmonics data monitor` GitHub Actions workflow (`.github/workflows/harmonics-data-monitor.yml`) runs `scripts/check_harmonics_releases.rb` on the 1st of each month. It fails, and GitHub notifies the user who last changed its cron line (or who last enabled it again), when a release newer than the known baseline appears or when a source cannot be checked. A manual run (`workflow_dispatch`) notifies the user who started it. When you adopt or acknowledge a release, bump `KNOWN_XTIDE_RELEASE` or `KNOWN_TICON_RELEASE` in that script. It also runs `scripts/build_noaa_station_types.rb --check`, which fails when NOAA's station types differ from `data/noaa_station_types.json`; run the script without `--check` and commit the file. The TICON baseline is 4 because TICON-4 is known, although we use TICON-3.

This repository is public, so GitHub disables the schedule automatically after 60 days with no repository activity. To enable it again, go to the Actions tab, select "Harmonics data monitor", and click **Enable workflow**, or run `gh workflow enable harmonics-data-monitor.yml -R jpr5/webcaltides`. To do a check at any time, run `gh workflow run harmonics-data-monitor.yml -R jpr5/webcaltides`.

To update XTide, add the new `.tcd` file to `data/` and point the `latest-xtide.tcd` symlink at it. To update TICON, add the new CSV to `data/` and rebuild `ticon.json` from it: `latest-ticon.json` points at `ticon.json`, which has no version in its name, so the symlink does not change. `scripts/build_ticon_dataset.rb` reads `data/TICON_3.csv` (`TICON_PATH`), so change that input path to the new CSV first (also its `source` label, "TICON-3 + GESLA-4"). Then update `scripts/pull_data.rb`, upload the new files to the `data-v1` GitHub release (for TICON, both the source CSV and the rebuilt `ticon.json`), and bump the `KNOWN_*_RELEASE` baseline in `scripts/check_harmonics_releases.rb`. The station caches use a checksum of the source files, so they rebuild automatically.

`data/noaa_station_types.json` (NOAA id => `harmonic` or `subordinate`, from NOAA's tide prediction station list) is written by `scripts/build_noaa_station_types.rb` and committed outside LFS. In the 2025-12-28 TCD, 41 XTide reference tide stations have a "(sub)" twin at the same point, so with the same id; the engine keeps the twin that matches how NOAA predicts the station (the reference for a harmonic station, the "(sub)" for a subordinate one), and the later one when NOAA does not list it. The file is part of the source checksum, so a new file rebuilds the caches too.

## Caching

All data is cached to `cache/` directory (Railway persistent volume in production):

### Cache File Types

| Type | Pattern | Lifecycle |
|------|---------|-----------|
| Tide/current data | `{type}_v{ver}_{id}_{YYYYMM}.json` | Monthly, pruned on write + startup |
| iCal calendars | `{type}_v{ver}_{id}_{YYYYMM}_{units}_{solar}_{lunar}.ics` | Monthly, pruned on write + startup |
| Station lists | `{type}_stations_v{ver}_{YYYY}Q{Q}_{provider}.json` | Quarterly, pruned on startup |
| NOAA current regions | `noaa_current_regions_{YYYY}Q{Q}.json` | Quarterly, pruned on startup |
| Lunar phases | `lunar_phases_{YYYY}.json` | Annual, keeps current + prior year |
| Timezone lookups | `tzs.json` | Permanent, never pruned |

### Cache Lifecycle

1. **Creation**: Cache files are written atomically (temp file + rename) to prevent partial reads in multi-process Puma
2. **Startup cleanup**: On boot, `cleanup_old_cache_files` deletes all files older than current month/quarter
3. **Month-rollover cleanup**: `cleanup_if_month_changed` triggers bulk cleanup on first request after a month rolls over (thread-safe, non-blocking)
4. **No background threads**: All cleanup is synchronous (startup or lazy on request) for multi-process safety

### Key Methods

- `WebCalTides.atomic_write(filename, content)` — atomic file write (temp + rename)
- `WebCalTides.cleanup_if_month_changed` — lazily trigger cleanup on month rollover (called per-request, no-op after first run in a month)
- `WebCalTides.cleanup_old_cache_files` — bulk delete all expired cache files

## Code Patterns

- `WebCalTides` module (in webcaltides.rb) contains core business logic
- Clients inherit from `Clients::Base`, include `TimeWindow` mixin
- Models use `from_hash`/`to_h` for JSON serialization with version numbers
- Extensive use of caching to minimize external API calls
- All times in UTC internally, converted for display

## Known Issues

- CHS station metadata is unreliable for determining data availability

## Testing

```bash
bundle exec rspec                              # Run full suite
bundle exec rspec spec/unit/                   # Unit tests only
bundle exec rspec --format documentation       # Verbose output
```

Tests use RSpec with VCR cassettes for HTTP mocking, Timecop for time freezing, and WebMock for request stubbing. Coverage reports are generated via SimpleCov.

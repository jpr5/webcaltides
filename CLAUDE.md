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

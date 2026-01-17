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

Requires a [Geonames](https://www.geonames.org/login) account for timezone lookups. Set username in `server.rb` configure block or via ENV.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GEONAMES_USERNAME` | Yes | Username for Geonames timezone lookups |
| `GOOGLE_MAPS_API_KEY` | Optional | Google Maps Static API key for station map thumbnails |

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

All data is cached to `cache/` directory:
- Station lists: quarterly refresh (`tide_stations_v{version}_{YYYY}Q{Q}.json`)
- Tide/current data: monthly (`tides_v{version}_{id}_{YYYYMM}.json`)
- Timezone lookups: permanent (`tzs.json`)
- Calendar files: monthly (`.ics` files)

## Code Patterns

- `WebCalTides` module (in webcaltides.rb) contains core business logic
- Clients inherit from `Clients::Base`, include `TimeWindow` mixin
- Models use `from_hash`/`to_h` for JSON serialization with version numbers
- Extensive use of caching to minimize external API calls
- All times in UTC internally, converted for display

## Known Issues

- Code is not thread-safe (acknowledged in comments, runs under Puma threads)
- CHS station metadata is unreliable for determining data availability

## Testing

No formal test suite currently. Manual testing via browser or curl.

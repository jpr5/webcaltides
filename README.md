# WebCalTides [![Tests](https://github.com/jpr5/webcaltides/actions/workflows/test.yml/badge.svg)](https://github.com/jpr5/webcaltides/actions/workflows/test.yml)

WebCalTides is a neat little Ruby/Sinatra server for putting tides, currents,
solar and lunar data on your calendar.

Written to replace `sailwx.info`'s "Tide calendars to go"
(`http://tides.mobilegeographics.com/`), which was awesome for what it was but
doesn't seem to work anymore.  Who knows, maybe it'll come back... but in the
meantime, enjoy!

This service can be found at [webcaltides.org](https://webcaltides.org).

## Setup

First, install the dependencies with `bundle install`.

### Environment Variables

The easiest way to configure API keys is to create a `.env` file in the project root:

```bash
# .env
GOOGLE_API_KEY=your_google_api_key_here
GEONAMES_USERNAME=your_geonames_username
GEOAPIFY_API_KEY=your_geoapify_key
```

The `.env` file is gitignored for security. Alternatively, you can set these as shell environment variables.

### Timezone Lookups

For accurate timezone lookups (lat/lon → timezone), configure one of these services:

**Google Maps Time Zone API** (recommended):
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable the "Time Zone API" (and optionally "Maps Static API" for map thumbnails)
3. Create an API key and restrict it to these APIs
4. Add `GOOGLE_API_KEY=your_key_here` to your `.env` file

**Geonames** (free fallback):
1. Register for a free account at [Geonames](https://www.geonames.org/login)
2. After registration, log in and [enable web services access](https://www.geonames.org/manageaccount) (one-time)
3. Add `GEONAMES_USERNAME=your_username` to your `.env` file

Google API is preferred (50x faster), but Geonames works as a fallback. Without either, timezone lookups will use region metadata and longitude approximation.

### Map Thumbnails (Optional)

Station cards can display location map thumbnails. Two services are supported:

**Google Maps Static API** (if you already set `GOOGLE_API_KEY` above, this works automatically)

**Geoapify** (free alternative):
1. Register at [Geoapify](https://www.geoapify.com/) (free tier: 3,000 requests/day)
2. Copy your API key from your project
3. Add `GEOAPIFY_API_KEY=your_key_here` to your `.env` file

If no map API is configured, a placeholder icon will be shown instead.

OR, just use this one at [webcaltides.org](https://webcaltides.org)!

## Invocation

`rackup` (development) or `RACK_ENV=production puma -C puma.rb` (production).
Writes to a cache directory, basically stores every bit of intermediate work to
minimize impact on external services.

## TODO

Check [server.rb](https://github.com/jpr5/webcaltides/blob/master/server.rb) header.

## Bugs/Patches

Use GitHub for issues, patches welcome.

## LICENSE

Free as in beer, IFF you give me visible credit!  Mmm, beer..

## CREDITS

Originally written by [Jordan Ritter](https://www.linkedin.com/in/jordanritter/),
with contributions from [Paul Schellenberg](https://github.com/PaulJSchellenberg)
to support Canadian stations.

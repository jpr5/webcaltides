# WebCalTides

WebCalTides is a neat little Ruby/Sinatra server for putting tides, currents,
solar and lunar data on your calendar.

Written to replace `sailwx.info`'s "Tide calendars to go"
(`http://tides.mobilegeographics.com/`), which was awesome for what it was but
doesn't seem to work anymore.  Who knows, maybe it'll come back... but in the
meantime, enjoy!

This service can be found at [webcaltides.org](https://webcaltides.org).

## Setup

First, `bundle install`.

Then you'll need a Geonames account for lat/long => timezone conversion --
they're free, [go get one](https://www.geonames.org/login).  After you register,
the API won't work immediately - there's some delay.  At some point after,
you'll need to log back in and one-time manually [enable web services
access](https://www.geonames.org/manageaccount).

Set the `GEONAMES_USERNAME` environment variable with your username.

### Map Thumbnails (Optional)

Station cards can display location map thumbnails. Two services are supported:

**Google Maps Static API** (preferred):
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable the "Maps Static API"
3. Create an API key and restrict it to the Static Maps API
4. Set `GOOGLE_MAPS_API_KEY` environment variable

**Geoapify** (free fallback):
1. Register at [Geoapify](https://www.geoapify.com/) (free, no credit card required)
2. Create a project and copy your API key (free tier: 3,000 requests/day)
3. Set `GEOAPIFY_API_KEY` environment variable

If neither key is configured, a placeholder icon will be shown instead of maps.

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

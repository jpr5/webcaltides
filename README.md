# WebCalTides

WebCalTides is a neat little Ruby/Sinatra server for putting tides, currents and
solar data on your calendar.

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

Then update `server.rb:Server#configure` with your username and you're off to
the races.

OR, just use this one at [webcaltides.org](https://webcaltides.org)!

## Invocation

`rackup` or `unicorn` works great.  Writes to a cache directory, basically
stores every bit of intermediate work.

## TODO

Check [server.rb](https://github.com/jpr5/webcaltides/blob/master/server.rb) header.

## Bugs/Patches

Use GitHub for issues, patches welcome.

## LICENSE

Free as in beer, IFF you give me visible credit!  Mmm, beer..

## CREDITS

Originally written by [Jordan Ritter](https://github.com/jpr5), with contributions
from [Paul Schellenberg](https://github.com/PaulJSchellenberg) to support Canadian
stations.

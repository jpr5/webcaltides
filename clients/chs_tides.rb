require_relative 'base'
require_relative '../models/station'
require_relative '../models/tide_data'

module Clients
    class ChsTides < Base

        API_URL            = 'https://api-iwls.dfo-mpo.gc.ca/api/v1'
        PUBLIC_STATION_URL = "https://www.tides.gc.ca/en/stations/%s"

        include TimeWindow

        ## CHS tides generation won't do more than 366 days.
        self.window_size = 12.months

        # Bump when the station list this client returns changes for the same upstream data, so the
        # quarterly tide station cache (WebCalTides.tide_station_cache_file) is rebuilt on deploy.
        # v2: stations without high/low predictions (no wlp-hilo series) are left out
        def self.station_list_version
            2
        end

        # A listing where more than this share of the stations has no wlp-hilo series is taken to
        # be an upstream format change (e.g. timeSeries dropped or renamed), not real retirements.
        # The January 2026 listing has 486 of 1570 (31%) without one; a format change gives ~100%.
        # 60% (942 of 1570) leaves room for DFO to retire about 450 more, nearly as many again,
        # before the guard trips.
        MAX_RETIRED_SHARE = 0.6

        # Stations DFO publishes high/low predictions for, i.e. whose metadata lists a wlp-hilo
        # time series.  For the others (e.g. Toronto, which only has observations and forecasts)
        # the wlp-hilo data request 404s, so they aren't offered.
        #
        # If the listing fails the share check above, the filter isn't applied: the last good
        # list is returned (or, without one, every station), and station_list_degraded? is true
        # so the caller doesn't cache it.
        def tide_stations
            @station_list_degraded = false
            return nil unless data = station_metadata

            hilo, other = data.partition { |js| hilo?(js) }

            if implausible_retired_share?(other.length, data.length)
                @station_list_degraded = true
                hilo = @last_good_hilo || data
            else
                @last_good_hilo = hilo
            end

            return hilo.map do |js|
                Models::Station.new(
                    name: js['officialName'],
                    alternate_names: [],
                    id: js['id'],
                    public_id: js['code'],
                    region: region_for(js['latitude'], js['longitude']),
                    location: [js["officialName"], "Canada"].join(", "),
                    lat: js['latitude'],
                    lon: js['longitude'],
                    url: PUBLIC_STATION_URL % [ js['code'] ],
                    provider: 'chs'
                )
            end
        end

        # True when the last tide_stations call returned a fallback list (see MAX_RETIRED_SHARE)
        def station_list_degraded?
            !!@station_list_degraded
        end

        # { id => name } of the stations left out of tide_stations, so existing subscriptions to
        # them can be told the station is retired rather than getting a 404.  nil if the list
        # couldn't be fetched or fails the share check, so the caller keeps the list it has.
        def retired_stations
            return nil unless data = station_metadata

            other = data.reject { |js| hilo?(js) }
            return nil if implausible_retired_share?(other.length, data.length)

            return other.to_h { |js| [js['id'], js['officialName']] }
        end

        def tide_data_for(station, around)
            from  = beginning_of_window(around).iso8601
            to    = end_of_window(around).iso8601
            url   = "#{API_URL}/stations/#{station.id}/data?time-series-code=wlp-hilo&from=#{from}&to=#{to}"

            logger.info "getting tide data from #{url}"

            begin
                return nil unless json = get_url(url)
            rescue Mechanize::ResponseCodeError => e
                if e.response_code == "404"
                    logger.warn "404 for station #{station.id}, skipping (may be temporary)"
                    return nil
                end
                raise
            end

            logger.debug "parsing tide predictions for #{station.id} from API #{API_URL}"
            data = begin
                JSON.parse(json)
            rescue JSON::ParserError
                nil
            end

            # A body that isn't a list of events (an HTML error page, an error object, a list of error
            # objects or of events without a date or value) is an upstream error, not "no data": keep
            # the station and return nothing, so nothing is cached
            unless data.is_a?(Array) && data.all? { |e| e.is_a?(Hash) && e['eventDate'].is_a?(String) && e['value'].is_a?(Numeric) }
                logger.error "!! unusable CHS tide data for station #{station.id}: expected a list of events, got #{json[0, 100].inspect}"
                return nil
            end

            # So this happened: station 5cebf1e23d0f4a073c4bbfb4 returned empty data.  It is type:
            # DISCONTINUED, operating: false, which would strongly imply we should filter those out
            # from the list, however a different station 5cebf1df3d0f4a073c4bbcb9 also has the same
            # type/operating but *DOES* return tide data.
            #
            # Upon further research, it turns out the CHS metadata is a fucking mess.  Of the tons
            # of possible indicators in the metadata, none are reliable as way to know if an
            # "active" server in the station list will return actual tide data.  This is to say,
            # some stations that are Temporary, Discontinued, or operating:false, etc, will return
            # data, while others that are status:OK, Permanent, etc, won't.  The only way to know is
            # to try to retrieve it.
            #
            # Stations without a wlp-hilo series are now left out of the list (see tide_stations),
            # but having the series still doesn't guarantee data, and there are API ratelimits and
            # over 1k stations to double-check.  So for the rest all we can do is (1) return nil to
            # the caller, and (2) nuke the station from the list post-facto.  This means bad
            # stations will show up in the search results until someone attempts to use one -- then
            # it will get nuked.
            #
            # Super lame.

            if data.length == 0
                logger.error "!! got empty tide data for station #{station.id}, nuking from list"
                WebCalTides.remove_tide_station(station.id)
                return nil
            end

            # The first event's type comes from comparing it with the second; a lone event is
            # called High, as there is nothing to compare it with
            prev_value = (data[1] || data[0])['value']

            return data.map do |jt|
                time = DateTime.parse(jt["eventDate"])
                td = Models::TideData.new(
                    type: jt['value'] >= prev_value ? "High" : "Low",
                    units: "m",
                    prediction: jt["value"],
                    time: time,
                    url: station.url + "/#{time.strftime("%Y-%m-%d")}"
                )
                prev_value = td.prediction
                td
            end
        end

        private

        # The parsed /stations listing, or nil (logged at error) if it can't be fetched or isn't a
        # non-empty list of station objects, so callers don't cache a bad response as a real list.
        def station_metadata
            url = "#{API_URL}/stations"

            logger.info "getting tide station list from #{url}"

            return nil unless json = get_url(url)

            logger.debug "parsing tide station list from API #{API_URL}"
            data = JSON.parse(json)

            unless data.is_a?(Array) && data.any? && data.all?(Hash)
                logger.error "!! unusable CHS station list from #{url}: expected a non-empty list of stations, got #{json[0, 100].inspect}"
                return nil
            end

            data
        rescue JSON::ParserError => e
            logger.error "!! unparseable CHS station list from #{url}: #{e.message[0, 100]}"
            nil
        end

        def implausible_retired_share?(retired, total)
            return false unless retired > MAX_RETIRED_SHARE * total

            logger.error "!! #{retired} of #{total} CHS stations have no wlp-hilo series (over #{(MAX_RETIRED_SHARE * 100).round}%), " \
                         "taking it as a listing format change: not filtering or updating the retired list"
            true
        end

        # A timeSeries of any other shape counts as no wlp-hilo series, so a reshaped listing trips
        # the MAX_RETIRED_SHARE guard instead of raising
        def hilo?(js)
            series = js['timeSeries']
            series.is_a?(Array) && series.any? { |ts| ts.is_a?(Hash) && ts['code'] == 'wlp-hilo' }
        end

        def region_for(lat, long)
            if long < -75 && long > -96 && lat < 64 && lat > 51
                'Hudson\'s Bay, Canada'
            elsif lat > 60
                'Northern Canada'
            elsif long < -120
                'Pacific Canada'
            elsif long > -75
                'Atlantic Canada'
            else
                'Canada'
            end
        end

    end
end

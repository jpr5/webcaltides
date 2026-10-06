require_relative 'base'
require_relative '../models/station'
require_relative '../models/tide_data'

module Clients
    # Kartverket (Norwegian Mapping Authority, Hydrographic Service) -- official Norwegian tide
    # predictions, from the keyless water level API at vannstand.kartverket.no.
    #
    # How often we fetch:
    # - station list (the permanent gauges): once per quarter (WebCalTides caches it to disk); if it
    #   fails, again after WebCalTides::TIDE_STATIONS_RETRY (1h)
    # - high/low predictions: one request per station per requested month, covering the whole
    #   13-month window (WebCalTides caches each successful result to disk); after an error (HTTP
    #   status, network failure or unusable response) or an empty window, not again for
    #   UNAVAILABLE_RETRY (1h, in memory, per process)
    #
    # Terms (API protocol rev. June 2025, p. 3, and kartverket.no "Vilkår for bruk"): open to
    # everybody without registration, licensed under CC BY 4.0.  Kartverket must be credited as
    # "© Kartverket", with a link to kartverket.no where possible, in every context the data is
    # used.  Cache static data and keep the number of requests low (the API is shared, ~20 req/s
    # for all users).  CC BY also asks us to say what we changed: we convert heights from cm to
    # metres or feet and present the high and low waters as calendar events; times stay in UTC as
    # Kartverket publishes them (the subscriber's calendar app shows them in its own zone).
    #
    # Times: we ask for UTC (tzone=0, dst=0), and every timestamp carries an explicit +00:00, so
    # no DST handling is needed.  Heights: the API returns cm above chart datum (refcode=cd); we
    # store them in metres.
    #
    # Predictions come from tide_request=locationdata at the gauge's own position, which returns
    # the gauge itself (delay 0, factor 1.00) and honours the 1000-day limit for high/low times.
    # tide_request=stationdata would take the station code, but silently truncates at 366 days,
    # one month short of our 13-month window.
    class KartverketTides < Base

        API_URL            = 'https://vannstand.kartverket.no/tideapi.php'
        PUBLIC_STATION_URL = 'https://kartverket.no/en/at-sea/se-havniva/result?latitude=%s&longitude=%s'
        HOME_URL           = 'https://www.kartverket.no/'
        LICENSE_URL        = 'https://creativecommons.org/licenses/by/4.0/'

        # The credit form required by the terms ("© Kartverket"), with the link and licence
        ATTRIBUTION = "© Kartverket (Norwegian Mapping Authority, Hydrographic Service), #{HOME_URL}, licensed under CC BY 4.0 (#{LICENSE_URL})"

        # CC BY: indicate changes
        CHANGES = "Heights converted from cm above chart datum to metres or feet, and high and low waters presented as calendar events; times unchanged, in UTC."

        DISCLAIMER = "NOT FOR NAVIGATION. Official tide predictions of Kartverket, distributed as they are; Kartverket takes no responsibility for their use."

        include TimeWindow

        def initialize(logger)
            super
            @unavailable       = {}
            @unavailable_mutex = Mutex.new
        end

        # Get a full year (1 month behind + now + 11 ahead), well inside the 1000-day high/low limit
        self.window_size = 13.months

        # How long to wait before asking Kartverket again for a station that errored or had no data
        # in a window.  The API is live (not regenerated files like BSH), so failures are more
        # likely transient; an hour still keeps a broken station to one request per hour.
        UNAVAILABLE_RETRY = 1.hour

        TIDE_TYPES = { 'high' => 'High', 'low' => 'Low' }.freeze

        # Transport failures get_url can raise (after its retries, for timeouts): no usable response,
        # as opposed to an HTTP error status (Mechanize::ResponseCodeError, rescued before these).
        # Mechanize::Error covers a truncated or cut-short body (ResponseReadError,
        # ChunkedTerminationError), an undecodable content-encoding and too many redirects.
        NETWORK_ERRORS = [
            Timeout::Error, SocketError, SystemCallError, OpenSSL::SSL::SSLError, EOFError, IOError,
            Net::HTTPBadResponse, Net::HTTP::Persistent::Error, Mechanize::Error
        ].freeze

        # Kartverket timestamps always carry an explicit offset; without one DateTime.parse would read UTC
        TIMESTAMP_WITH_OFFSET = /\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:?\d{2})\z/

        # Feed-level credit + disclaimer
        def self.feed_description(_tides = nil)
            "#{ATTRIBUTION}. #{CHANGES} #{DISCLAIMER}"
        end

        # Per-event credit
        def self.event_description(_tide = nil)
            "#{ATTRIBUTION}. NOT FOR NAVIGATION."
        end

        # Station id: 'NO__' + Kartverket's three-letter station code, e.g. Bergen (BGO) -> NO__BGO
        def self.station_id_for(code)
            "NO__#{code}"
        end

        # Plain-ASCII spellings of a Norwegian name, so "Tromso"/"Tromsoe" and "Alesund"/"Aalesund"
        # find Tromsø and Ålesund.
        ASCII_FOLDS = [
            { 'æ' => 'ae', 'ø' => 'o',  'å' => 'a',  'Æ' => 'Ae', 'Ø' => 'O',  'Å' => 'A'  },
            { 'æ' => 'ae', 'ø' => 'oe', 'å' => 'aa', 'Æ' => 'Ae', 'Ø' => 'Oe', 'Å' => 'Aa' }
        ].freeze

        def self.ascii_names(name)
            ASCII_FOLDS.map { |fold| name.gsub(/[æøåÆØÅ]/, fold) }.uniq - [name]
        end

        def tide_stations
            url = "#{API_URL}?#{URI.encode_www_form(tide_request: 'stationlist', type: 'perm', lang: 'en')}"

            logger.info "getting tide station list from #{url}"

            unless xml = get_url(url)
                logger.error "!! got no Kartverket tide station list from #{url}"
                return nil
            end

            # nil (not []) when the list is missing, unreadable or empty, so the caller doesn't cache
            # "no Norwegian gauges" as healthy.  Entries are validated one by one below; if every one
            # is skipped, the result is [] and the caller does cache it.
            logger.debug "parsing tide station list from API #{API_URL}"
            doc = parse_xml(xml)
            if doc.nil? || (error = api_error(doc))
                logger.error "!! Kartverket tide station list at #{url} unusable (#{error || 'not XML'}), body starts #{xml[0, 100].inspect}"
                return nil
            end

            locations = doc.xpath('/tide/stationinfo/location')
            if locations.empty?
                logger.error "!! Kartverket tide station list at #{url} has no stations, body starts #{xml[0, 100].inspect}"
                return nil
            end

            stations = locations.filter_map do |loc|
                code, name = loc['code'].to_s.strip, loc['name'].to_s.strip
                lat, lon   = Float(loc['latitude'], exception: false), Float(loc['longitude'], exception: false)

                unless code.match?(/\A[A-Z]{3}\z/) && name.present? && lat && lon
                    logger.warn "skipping Kartverket station without code, name or position: #{loc.to_s[0, 200]}"
                    next
                end

                Models::Station.new(
                    name: name,
                    alternate_names: self.class.ascii_names(name),
                    id: self.class.station_id_for(code),
                    public_id: code,
                    region: 'Norway',
                    location: [name, 'Norway'].join(", "),
                    lat: lat,
                    lon: lon,
                    url: PUBLIC_STATION_URL % [ loc['latitude'], loc['longitude'] ],
                    provider: 'kartverket'
                )
            end

            return stations
        end

        def tide_data_for(station, around)
            from = beginning_of_window(around)
            to   = end_of_window(around)

            # A station-level failure (error, wrong station, unreadable response) blocks every window;
            # an empty window only this one
            window_key = "#{station.id}@#{around.utc.strftime('%Y%m')}"
            if (until_time = unavailable_until(station.id) || unavailable_until(window_key))
                logger.debug "skipping Kartverket tide data for #{station.id}: unavailable, next try after #{until_time}"
                return nil
            end

            url = "#{API_URL}?" + URI.encode_www_form(
                tide_request: 'locationdata', lat: station.lat, lon: station.lon, datatype: 'tab',
                refcode: 'cd', lang: 'en', tzone: 0, dst: 0,
                fromtime: from.strftime('%Y-%m-%dT%H:%M'), totime: to.strftime('%Y-%m-%dT%H:%M')
            )

            logger.info "getting tide data from #{url}"

            begin
                unless xml = get_url(url)
                    logger.error "!! got no Kartverket tide data from #{url}"
                    return unavailable!(station.id)
                end
            rescue Mechanize::ResponseCodeError => e
                # Back off for any HTTP error, so an outage doesn't turn every feed request into another call
                unavailable!(station.id)
                if e.response_code == "404"
                    logger.warn "404 for station #{station.id}, skipping (may be temporary), next try in #{UNAVAILABLE_RETRY.inspect}"
                    return nil
                end
                raise
            rescue *NETWORK_ERRORS => e
                # get_url has already retried timeouts; back off so an outage costs one call per
                # station per UNAVAILABLE_RETRY instead of a full retry sequence on every feed request
                logger.error "!! Kartverket tide data for station #{station.id} unreachable (#{e.class}: #{e.message}), next try in #{UNAVAILABLE_RETRY.inspect}"
                unavailable!(station.id)
                raise
            end

            logger.debug "parsing tide predictions for #{station.id} from API #{API_URL}"
            doc = parse_xml(xml)
            if doc.nil? || (error = api_error(doc))
                logger.error "!! Kartverket tide data for station #{station.id} at #{url} unusable (#{error || 'not XML'}), body starts #{xml[0, 100].inspect}"
                return unavailable!(station.id)
            end

            # The position must resolve to this gauge's own code with no time or height correction
            # (delay 0, factor 1).  Predictions Kartverket copies unchanged from another gauge (a
            # different obscode, e.g. Sandnes from Stavanger) are accepted.
            location = doc.at_xpath('/tide/locationdata/location')
            unless location && location['code'] == station.public_id && location['delay'].to_f == 0 && location['factor'].to_f == 1
                logger.error "!! Kartverket tide data for station #{station.id} at #{url} is not for gauge #{station.public_id}: #{location.to_s[0, 200]}"
                return unavailable!(station.id)
            end

            data   = doc.at_xpath('/tide/locationdata/data[@type="prediction"]')
            datum  = doc.at_xpath('/tide/locationdata/reflevelcode')&.text.to_s.strip
            unit   = data && data['unit']
            heights = datum.casecmp?('cd') && unit == 'cm'
            unless heights
                logger.warn "omitting heights of Kartverket data for station #{station.id}: unexpected datum #{datum.inspect} or unit #{unit.inspect}"
            end

            skipped   = []
            no_height = 0
            tides = Array(data&.xpath('waterlevel')).filter_map do |wl|
                type   = TIDE_TYPES[wl['flag']]
                time   = parse_timestamp(wl['time']) if type
                height = Float(wl['value'], exception: false)

                unless type && time
                    skipped << wl
                    next
                end

                no_height += 1 if heights && height.nil?

                Models::TideData.new(
                    type: type,
                    units: "m",
                    prediction: (heights && height) ? (height / 100.0).round(3) : nil,
                    time: time,
                    url: station.url
                )
            end

            if skipped.any?
                logger.warn "skipping #{skipped.length} Kartverket events for station #{station.id} with unknown flag or bad timestamp, e.g. #{skipped.first.to_s[0, 200]}"
            end

            if no_height > 0
                logger.warn "keeping #{no_height} Kartverket events for station #{station.id} without a usable height"
            end

            tides = tides.select { |td| td.time >= from && td.time <= to }.sort_by(&:time)

            if tides.empty?
                logger.error "!! no Kartverket tide data for station #{station.id} between #{from.strftime('%Y-%m-%d')} and #{to.strftime('%Y-%m-%d')}"
                return unavailable!(window_key)
            end

            return tides
        end

        private

        def parse_xml(xml)
            Nokogiri::XML(xml) { |config| config.strict.nonet }
        rescue Nokogiri::XML::SyntaxError
            nil
        end

        # Kartverket reports errors as HTTP 200 with an <error> element, at the top level
        # (e.g. "Position outside area") or inside the request element
        def api_error(doc)
            doc.at_xpath('//error')&.text&.strip.presence
        end

        # Same instant as published, normalized to UTC.  nil if unusable.
        def parse_timestamp(timestamp)
            return nil unless timestamp.is_a?(String) && timestamp.match?(TIMESTAMP_WITH_OFFSET)
            DateTime.parse(timestamp).new_offset(0)
        rescue Date::Error
            nil
        end

        # Negative cache for stations (or station + month) without usable data, so we don't refetch
        # on every request (WebCalTides only caches successful results).  Per process, in memory.
        def unavailable!(key)
            @unavailable_mutex.synchronize { @unavailable[key] = Time.current.utc + UNAVAILABLE_RETRY }
            return nil
        end

        def unavailable_until(key)
            @unavailable_mutex.synchronize do
                until_time = @unavailable[key] or return nil
                return until_time if Time.current.utc < until_time
                @unavailable.delete(key)
                nil
            end
        end

    end
end

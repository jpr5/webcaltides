require 'json'
require_relative 'base'
require_relative '../models/station'
require_relative '../models/tide_data'

module Clients
    # Marks a tide list that doesn't cover its whole window (the source hasn't published the rest
    # yet).  WebCalTides serves it but doesn't cache it to disk for the month.
    module PartialWindow
        def partial? = true
    end

    # Rijkswaterstaat (Netherlands) -- astronomical tide predictions (high and low waters) for the
    # Dutch tide gauges, from RWS's keyless WaterWebservices (DD API 2.0, POST with a JSON body).
    #
    # How often we fetch:
    # - station list (the WaterWebservices catalogue, about 2 MB, then one count of the predictions
    #   for all its tide locations, about 150 KB): once per quarter (WebCalTides caches it to disk);
    #   if it fails, again after WebCalTides::TIDE_STATIONS_RETRY (1h)
    # - high/low predictions: one request per station per requested month, covering the whole
    #   13-month window (WebCalTides caches each complete result to disk).  After an error (HTTP
    #   status, network failure, unusable response) or an empty window, that window is not asked
    #   for again for UNAVAILABLE_RETRY (1h, in memory, per process); the station's other windows
    #   are not affected.
    # - a window that runs past what RWS has published (to the end of next year: 2027-12-31 on
    #   6 Oct 2026) is served as far as it goes, but marked PartialWindow so it isn't cached for the
    #   month; we keep it in memory for PARTIAL_RETRY (6h) and then ask again, so the next year
    #   shows up soon after RWS publishes it.  If that refresh fails, the old list is served (with
    #   a warning) instead: it isn't on disk, so without it every Dutch feed would fail during an RWS
    #   outage for most of each year.  A partial list is dropped PARTIAL_STALE_LIMIT (7 days) after
    #   it was fetched.
    #
    # Terms (rijkswaterstaatdata.nl/waterdata, "Uptime, Fair use en Copyright"): the content of the
    # WaterWebservices is CC0, so reuse needs no credit (we give a short one anyway).  Fair use:
    # RWS may limit access for unreasonable use, hence the caching above.  No uptime guarantee and
    # "not suitable for critical applications", so: not for navigation.
    #
    # Times: every timestamp carries a fixed +01:00 (Dutch winter time) all year; RWS never
    # applies summer time.  We honour the explicit offset and keep the instant in UTC.  Do not
    # "fix" this with Europe/Amsterdam rules: that would shift every summer event by an hour.
    #
    # Heights: centimetres above NAP (Normaal Amsterdams Peil, the Dutch land-survey datum), not
    # chart datum (LAT).  RWS publishes no NAP-to-LAT offset per station in this service, so we
    # don't convert: heights are given in metres (or feet) above NAP and labelled "NAP" wherever
    # they are shown.  The 13 offshore points that RWS gives relative to MSL (GETETBRKDMSL2) are
    # left out.
    class RijkswaterstaatTides < Base

        API_URL       = 'https://ddapi20-waterwebservices.rijkswaterstaat.nl'
        CATALOGUE_URL = "#{API_URL}/METADATASERVICES/OphalenCatalogus"
        COUNT_URL     = "#{API_URL}/ONLINEWAARNEMINGENSERVICES/OphalenAantalWaarnemingen"
        DATA_URL      = "#{API_URL}/ONLINEWAARNEMINGENSERVICES/OphalenWaarnemingen"
        HOME_URL      = 'https://waterinfo.rws.nl'
        LICENSE_URL   = 'https://creativecommons.org/publicdomain/zero/1.0/'

        # The height datum, shown next to every height
        HEIGHT_DATUM = 'NAP'

        ATTRIBUTION = "Source: Rijkswaterstaat (Netherlands), astronomical tide predictions from the WaterWebservices (#{HOME_URL}), CC0 (#{LICENSE_URL})"

        CHANGES = "Heights are above NAP (Normaal Amsterdams Peil, the Dutch land-survey datum), not chart datum (LAT): converted from centimetres to metres, or to feet. Times converted from the fixed +01:00 RWS uses all year to UTC. High and low waters presented as calendar events."

        # The same, for a window without usable heights (they are left out)
        CHANGES_WITHOUT_HEIGHTS = "Heights left out (none usable in the data received). Times converted from the fixed +01:00 RWS uses all year to UTC. High and low waters presented as calendar events."

        DISCLAIMER = "NOT FOR NAVIGATION. Computed astronomical tide: weather (wind, air pressure) is not included. Rijkswaterstaat says its data service has no uptime guarantee and is not suitable for critical applications, and that use is at your own risk."

        include TimeWindow

        def initialize(logger)
            super
            @unavailable       = {}
            @unavailable_mutex = Mutex.new
            @partial           = {}
        end

        # Get a full year (1 month behind + now + 11 ahead).  RWS publishes to the end of next year.
        self.window_size = 13.months

        # How long to wait before asking again for a window that errored or had no data.  A live
        # service, so failures are likely transient; an hour still keeps a broken window to one
        # request per hour.
        UNAVAILABLE_RETRY = 1.hour

        # How long a partial window (past the published horizon) is reused before asking again
        PARTIAL_RETRY = 6.hours

        # How long a partial window is kept, to serve when its refresh fails
        PARTIAL_STALE_LIMIT = 7.days

        # A location is listed if RWS has high/low waters for it in this much time from today
        CURRENT_PERIOD = 1.month

        # A window is partial if its first or last event is further than this from its edge.  High
        # and low waters are at most about 13 hours apart, so a day leaves a margin.
        PARTIAL_GAP = 1.day

        MAX_RETRIES = Base::MAX_RETRIES

        # Transport failures the POST can raise (after its retries, for timeouts): no usable
        # response, as opposed to an HTTP error status (Mechanize::ResponseCodeError, rescued
        # before these).  Mechanize::Error covers a truncated or cut-short body (ResponseReadError,
        # ChunkedTerminationError), an undecodable content-encoding and too many redirects.
        NETWORK_ERRORS = [
            Timeout::Error, SocketError, SystemCallError, OpenSSL::SSL::SSLError, EOFError, IOError,
            Net::HTTPBadResponse, Net::HTTP::Persistent::Error, Mechanize::Error
        ].freeze

        TIDE_TYPES = { 'hoogwater' => 'High', 'laagwater' => 'Low' }.freeze

        # What a high/low water series is (AquoMetadata): computed tide extremes vs NAP
        PROCESS_TYPE = 'astronomisch'
        GROUPING     = 'GETETBRKD2'
        HEIGHT       = { quantity: 'WATHTE', datum: 'NAP', unit: 'cm' }.freeze
        TYPE_SERIES  = 'GETETTPE'

        # RWS location codes are lower-case words joined by dots ("denhelder.marsdiep")
        STATION_CODE = /\A[a-z0-9]+(\.[a-z0-9]+)*\z/
        TIMESTAMP    = /\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?[+-]\d{2}:\d{2}\z/

        # A height code RWS uses for a gap ("hiaatwaarde"), and the magnitude it gives missing values
        GAP_QUALITY = '99'
        MAX_HEIGHT_CM = 10_000

        # Locations in RWS's network that are not in the Netherlands (the station's region and the
        # country in its location).  RWS gives no country per location; these are the three of its
        # 101 high/low water gauges outside the Netherlands (live catalogue, Oct 2026).
        COUNTRIES = {
            'antwerpen.prosperpolder' => 'Belgium',
            'knock'                   => 'Germany',
            'pogum'                   => 'Germany'
        }.freeze

        # Feed-level credit + what we changed + disclaimer
        def self.feed_description(tides = nil)
            changes = tides.present? && tides.all? { |td| td.prediction.nil? } ? CHANGES_WITHOUT_HEIGHTS : CHANGES
            "#{ATTRIBUTION}. #{changes} #{DISCLAIMER}"
        end

        # Per-event credit
        def self.event_description(tide = nil)
            datum = tide&.prediction.nil? ? nil : " Height above #{HEIGHT_DATUM}, not chart datum."
            "Source: Rijkswaterstaat, CC0.#{datum} NOT FOR NAVIGATION."
        end

        # Shown after every height ("1.74 m NAP")
        def self.height_datum
            HEIGHT_DATUM
        end

        # Station id: 'NL__' + RWS's location code, e.g. Stavenisse (stavenisse) -> NL__stavenisse
        def self.station_id_for(code)
            "NL__#{code}"
        end

        # Other spellings to search by: plain ASCII, and TICON's "<name>, NLD" form
        def self.alternate_names(code, name)
            names = [ActiveSupport::Inflector.transliterate(name)]
            names << "#{name}, NLD" unless COUNTRIES.key?(code)
            names.uniq - [name]
        end

        CATALOGUE_QUERY = { CatalogusFilter: { Grootheden: true, Groeperingen: true, Hoedanigheden: true, ProcesTypes: true } }.freeze

        def tide_stations
            logger.info "getting tide station list from #{CATALOGUE_URL}"

            unless (json = post_json(CATALOGUE_URL, CATALOGUE_QUERY)).present?
                logger.error "!! got no Rijkswaterstaat tide station list from #{CATALOGUE_URL}"
                return nil
            end

            # nil (not []) when the list is missing, unreadable, empty or has no usable location, so
            # the caller doesn't cache "no Dutch stations" as healthy.  Locations are validated one
            # by one below; a bad one is skipped, not the whole list.
            logger.debug "parsing tide station list from #{CATALOGUE_URL}"
            catalogue = parse_json(json)
            metadata, links, locations = catalogue && %w[AquoMetadataLijst AquoMetadataLocatieLijst LocatieLijst].map { |k| catalogue[k] }
            unless catalogue && catalogue['Succesvol'] == true && [metadata, links, locations].all? { |l| l.is_a?(Array) && l.all?(Hash) }
                logger.error "!! Rijkswaterstaat tide station list at #{CATALOGUE_URL} unusable (not a catalogue), body starts #{text(json)[0, 100].inspect}"
                return nil
            end

            # The high/low water heights vs NAP; a location with them also has the matching types
            series = metadata.select { |m| height_series?(m) }.map { |m| m['AquoMetadata_MessageID'] }
            location_ids = links.select { |l| series.include?(l['AquoMetaData_MessageID']) }.map { |l| l['Locatie_MessageID'] }.to_set
            listed = locations.select { |l| location_ids.include?(l['Locatie_MessageID']) }

            if listed.empty?
                logger.error "!! Rijkswaterstaat tide station list at #{CATALOGUE_URL} has no locations with high/low water predictions vs NAP"
                return nil
            end

            stations = listed.filter_map do |l|
                code, name = l['Code'], text(l['Naam']).strip
                lat, lon   = Float(l['Lat'], exception: false), Float(l['Lon'], exception: false)

                unless code.to_s.match?(STATION_CODE) && name.present? && lat && lon
                    logger.warn "skipping Rijkswaterstaat location without code, name or position: #{l.to_json[0, 200]}"
                    next
                end

                country = COUNTRIES.fetch(code, 'Netherlands')
                Models::Station.new(
                    name: name,
                    alternate_names: self.class.alternate_names(code, name),
                    id: self.class.station_id_for(code),
                    public_id: code,
                    region: country,
                    location: "#{name}, #{country}",
                    lat: lat,
                    lon: lon,
                    url: HOME_URL,
                    provider: 'rws'
                )
            end.uniq(&:id)

            if stations.empty?
                logger.error "!! Rijkswaterstaat tide station list at #{CATALOGUE_URL} has no usable locations"
                return nil
            end

            # The catalogue also lists locations whose predictions stopped (Beerkanaal: 2015), which
            # only ever answer 204 and would 404 as feeds.  Its metadata doesn't tell them apart, so
            # one count of the coming month's predictions, for all locations at once, does.
            current = current_codes(stations.map(&:public_id)) or return nil
            dead, stations = stations.partition { |s| !current.include?(s.public_id) }
            logger.info "leaving out #{dead.length} Rijkswaterstaat locations without current predictions: #{dead.map(&:public_id).join(', ')}" if dead.any?

            if stations.empty?
                logger.error "!! Rijkswaterstaat tide station list at #{CATALOGUE_URL} has no locations with current predictions"
                return nil
            end

            return stations
        end

        def tide_data_for(station, around)
            # Every failure is kept to this window: one bad window must not block the station's others
            window_key = "#{station.id}@#{around.utc.strftime('%Y%m')}"
            partial, fresh = partial_for(window_key)

            if partial && fresh
                logger.debug "reusing partial Rijkswaterstaat tide data for #{station.id} around #{around.utc.strftime('%Y-%m')}"
                return partial
            end

            tides = begin
                fetch_window(station, around, window_key)
            rescue Mechanize::ResponseCodeError, *NETWORK_ERRORS
                raise unless partial
            end

            if tides
                drop_partial(window_key) unless tides.respond_to?(:partial?)
                return tides
            end

            return nil unless partial

            logger.warn "serving stale partial Rijkswaterstaat tide data for station #{station.id} around #{around.utc.strftime('%Y-%m')}: refreshing it failed"
            return partial
        end

        private

        # The window's tides, or nil (after an error or for a window without data, which is then
        # not asked for again for UNAVAILABLE_RETRY).  Re-raises HTTP and network errors.
        def fetch_window(station, around, window_key)
            from = beginning_of_window(around)
            to   = end_of_window(around)

            if (until_time = unavailable_until(window_key))
                logger.debug "skipping Rijkswaterstaat tide data for #{station.id} around #{around.utc.strftime('%Y-%m')}: unavailable, next try after #{until_time}"
                return nil
            end

            code = station.public_id.to_s
            unless code.match?(STATION_CODE)
                logger.error "!! Rijkswaterstaat station #{station.id} has an unusable location code #{code.inspect}"
                return unavailable!(window_key)
            end

            query = {
                Locatie: { Code: code },
                AquoPlusWaarnemingMetadata: { AquoMetadata: { ProcesType: PROCESS_TYPE, Groepering: { Code: GROUPING } } },
                Periode: { Begindatumtijd: api_time(from), Einddatumtijd: api_time(to) }
            }

            logger.info "getting tide data for #{station.id} from #{DATA_URL} between #{from.strftime('%Y-%m-%d')} and #{to.strftime('%Y-%m-%d')}"

            begin
                json = post_json(DATA_URL, query)
            rescue Mechanize::ResponseCodeError => e
                logger.error "!! Rijkswaterstaat tide data for station #{station.id} around #{around.utc.strftime('%Y-%m')} failed with HTTP #{e.response_code}, next try in #{UNAVAILABLE_RETRY.inspect}"
                unavailable!(window_key)
                raise
            rescue *NETWORK_ERRORS => e
                # post_json has already retried timeouts; back off so an outage costs one call per
                # window per UNAVAILABLE_RETRY instead of a full retry sequence on every feed request
                logger.error "!! Rijkswaterstaat tide data for station #{station.id} around #{around.utc.strftime('%Y-%m')} unreachable (#{e.class}: #{e.message}), next try in #{UNAVAILABLE_RETRY.inspect}"
                unavailable!(window_key)
                raise
            end

            # RWS answers 204 No Content for a period without data (past its published horizon)
            if json.blank?
                logger.warn "no Rijkswaterstaat tide data (empty response) for station #{station.id} between #{from.strftime('%Y-%m-%d')} and #{to.strftime('%Y-%m-%d')}, next try in #{UNAVAILABLE_RETRY.inspect}"
                return unavailable!(window_key)
            end

            logger.debug "parsing tide predictions for #{station.id} from #{DATA_URL}"
            data = parse_json(json)
            lists = data && data['WaarnemingenLijst']
            unless data && data['Succesvol'] == true && lists.is_a?(Array) && lists.all? { |l| l.is_a?(Hash) && l['AquoMetadata'].is_a?(Hash) && l['MetingenLijst'].is_a?(Array) }
                logger.error "!! Rijkswaterstaat tide data for station #{station.id} unusable (not a list of series), body starts #{text(json)[0, 100].inspect}"
                return unavailable!(window_key)
            end

            if (other = lists.map { |l| field(l, 'Locatie', 'Code') }.find { |c| c != code })
                logger.error "!! Rijkswaterstaat tide data for station #{station.id} has a series for #{other.inspect}"
                return unavailable!(window_key)
            end

            types   = lists.find { |l| field(l, 'AquoMetadata', 'Typering', 'Code') == TYPE_SERIES }
            heights = lists.find { |l| field(l, 'AquoMetadata', 'Grootheid', 'Code') == HEIGHT[:quantity] }

            unless types
                logger.error "!! Rijkswaterstaat tide data for station #{station.id} has no high/low water types, series: #{lists.map { |l| field(l, 'AquoMetadata', 'Parameter_Wat_Omschrijving') }.inspect}"
                return unavailable!(window_key)
            end

            if heights && !(field(heights, 'AquoMetadata', 'Hoedanigheid', 'Code') == HEIGHT[:datum] && field(heights, 'AquoMetadata', 'Eenheid', 'Code') == HEIGHT[:unit])
                logger.warn "omitting heights of Rijkswaterstaat data for station #{station.id}: datum #{field(heights, 'AquoMetadata', 'Hoedanigheid', 'Code').inspect}, unit #{field(heights, 'AquoMetadata', 'Eenheid', 'Code').inspect} (expected NAP, cm)"
                heights = nil
            end
            logger.warn "no heights in Rijkswaterstaat data for station #{station.id}, keeping times only" unless heights

            # Heights by instant (the two series share their timestamps)
            cm = (heights ? heights['MetingenLijst'] : []).each_with_object({}) do |m, h|
                time = parse_timestamp(m.is_a?(Hash) && m['Tijdstip']) or next
                h[time] = height_cm(m)
            end

            skipped   = []
            no_height = 0
            tides = types['MetingenLijst'].filter_map do |m|
                type = TIDE_TYPES[m.is_a?(Hash) && text(field(m, 'Meetwaarde', 'Waarde_Alfanumeriek')).strip.downcase]
                time = parse_timestamp(m.is_a?(Hash) && m['Tijdstip']) if type

                unless type && time
                    skipped << m
                    next
                end

                height = cm[time]
                no_height += 1 if heights && height.nil?

                Models::TideData.new(
                    type: type,
                    units: "m",
                    prediction: height && (height / 100.0).round(2),
                    time: time,
                    url: station.url
                )
            end

            if skipped.any?
                logger.warn "skipping #{skipped.length} Rijkswaterstaat events for station #{station.id} with unknown type or bad timestamp, e.g. #{skipped.first.to_json[0, 200]}"
            end

            if no_height > 0
                logger.warn "keeping #{no_height} Rijkswaterstaat events for station #{station.id} without a usable height"
            end

            tides = tides.select { |td| td.time >= from && td.time <= to }.uniq { |td| [td.time, td.type] }.sort_by(&:time)

            if tides.empty?
                logger.error "!! no Rijkswaterstaat tide data for station #{station.id} between #{from.strftime('%Y-%m-%d')} and #{to.strftime('%Y-%m-%d')}"
                return unavailable!(window_key)
            end

            # Past the published horizon: serve what there is, but not as the complete month
            if tides.first.time > from + PARTIAL_GAP || tides.last.time < to - PARTIAL_GAP
                logger.info "partial Rijkswaterstaat tide data for station #{station.id}: #{tides.first.time.strftime('%Y-%m-%d')} to #{tides.last.time.strftime('%Y-%m-%d')} of #{from.strftime('%Y-%m-%d')} to #{to.strftime('%Y-%m-%d')}, not caching it for the month; next try in #{PARTIAL_RETRY.inspect}"
                tides.extend(PartialWindow)
                partial!(window_key, tides)
            end

            return tides
        end

        # POST a JSON body, with the same retries as Base#get_url (gateway errors and timeouts).
        # Returns the body ("" for 204 No Content).
        def post_json(url, body)
            agent   = Mechanize.new
            retries = 0

            begin
                agent.post(url, body.to_json, 'Content-Type' => 'application/json', 'Accept' => 'application/json').body.to_s
            rescue Mechanize::ResponseCodeError => e
                if (e.response_code == "502" || e.response_code == "504") && retries < MAX_RETRIES
                    retries += 1
                    delay = rand(0.5..(2.0 ** retries))
                    logger.warn "#{e.response_code} from #{url}, retry #{retries}/#{MAX_RETRIES} in #{delay.round(1)}s"
                    sleep delay
                    retry
                end

                logger.error "POST failed after #{retries} retries: #{e.detailed_message}"
                raise e
            rescue Net::OpenTimeout, Net::ReadTimeout => e
                if retries < MAX_RETRIES
                    retries += 1
                    delay = rand(0.5..(2.0 ** retries))
                    logger.warn "timeout for #{url}, retry #{retries}/#{MAX_RETRIES} in #{delay.round(1)}s"
                    sleep delay
                    retry
                end

                logger.error "timeout after #{retries} retries: #{e.message}"
                raise e
            end
        end

        # Mechanize hands us the body as binary; RWS sends UTF-8.  Invalid bytes are replaced.
        def text(str)
            str.to_s.dup.force_encoding(Encoding::UTF_8).scrub
        end

        # Hash#dig that gives nil (instead of raising) where the response has a non-object in the way
        def field(obj, *keys)
            keys.reduce(obj) { |o, k| o.is_a?(Hash) ? o[k] : nil }
        end

        def parse_json(json)
            JSON.parse(text(json))
        rescue JSON::ParserError
            nil
        end

        # The codes (of those given) with high/low waters from today to CURRENT_PERIOD ahead, as a
        # Set; nil (logged) if the count is unusable.  RWS leaves out a location without any.
        def current_codes(codes)
            from  = Time.current.utc.beginning_of_day
            query = {
                LocatieLijst: codes.map { |c| { Code: c } },
                AquoMetadataLijst: [{ ProcesType: PROCESS_TYPE, Groepering: { Code: GROUPING }, Typering: { Code: TYPE_SERIES } }],
                Groeperingsperiode: 'Jaar',
                Periode: { Begindatumtijd: api_time(from), Einddatumtijd: api_time(from + CURRENT_PERIOD) }
            }

            logger.info "getting prediction count for #{codes.length} Rijkswaterstaat locations from #{COUNT_URL}"
            json  = post_json(COUNT_URL, query)
            data  = parse_json(json)
            lists = data && data['AantalWaarnemingenPerPeriodeLijst']
            unless data && data['Succesvol'] == true && lists.is_a?(Array) && lists.all?(Hash)
                logger.error "!! Rijkswaterstaat prediction count at #{COUNT_URL} unusable (not a list of counts), body starts #{text(json)[0, 100].inspect}"
                return nil
            end

            lists.select { |l| (counts = l['AantalMetingenPerPeriodeLijst']).is_a?(Array) && counts.any? { |c| (n = field(c, 'AantalMetingen')).is_a?(Numeric) && n > 0 } }
                 .map { |l| field(l, 'Locatie', 'Code') }.to_set
        end

        def height_series?(m)
            m.is_a?(Hash) && m['ProcesType'] == PROCESS_TYPE && field(m, 'Groepering', 'Code') == GROUPING &&
                field(m, 'Grootheid', 'Code') == HEIGHT[:quantity] && field(m, 'Hoedanigheid', 'Code') == HEIGHT[:datum]
        end

        # The height in cm, or nil if missing, a gap value or out of range
        def height_cm(m)
            return nil if field(m, 'WaarnemingMetadata', 'Kwaliteitswaardecode') == GAP_QUALITY
            value = field(m, 'Meetwaarde', 'Waarde_Numeriek')
            value.is_a?(Numeric) && value.abs < MAX_HEIGHT_CM ? value : nil
        end

        # RWS accepts any ISO 8601 offset; we send UTC
        def api_time(time)
            time.utc.strftime('%Y-%m-%dT%H:%M:%S.000+00:00')
        end

        # The published instant (RWS gives +01:00), in UTC.  nil if unusable.
        def parse_timestamp(timestamp)
            return nil unless timestamp.is_a?(String) && timestamp.match?(TIMESTAMP)
            DateTime.iso8601(timestamp).new_offset(0)
        rescue Date::Error
            nil
        end

        # Negative cache for windows without usable data, so we don't refetch on every request
        # (WebCalTides only caches successful results).  Per process, in memory.
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

        # Partial windows, reused until PARTIAL_RETRY and kept as a fallback until
        # PARTIAL_STALE_LIMIT.  Per process, in memory.  Feeds ask for the current month, so an old
        # month is rarely asked for again: drop windows past the limit on each add, or each would
        # keep its whole event list for the life of the process.
        def partial!(key, tides)
            @unavailable_mutex.synchronize do
                now = Time.current.utc
                @partial.delete_if { |_, (fetched, _)| now >= fetched + PARTIAL_STALE_LIMIT }
                @partial[key] = [now, tides]
            end
        end

        # [tides, fresh?] for a kept partial window, or nil
        def partial_for(key)
            @unavailable_mutex.synchronize do
                fetched, tides = @partial[key]
                return nil unless fetched
                now = Time.current.utc
                return [tides, now < fetched + PARTIAL_RETRY] if now < fetched + PARTIAL_STALE_LIMIT
                @partial.delete(key)
                nil
            end
        end

        def drop_partial(key)
            @unavailable_mutex.synchronize { @partial.delete(key) }
        end

    end
end

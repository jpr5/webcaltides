require_relative 'base'
require_relative '../models/station'
require_relative '../models/tide_data'

module Clients
    # Marine Institute (Ireland) -- tide predictions for the Irish National Tide Gauge Network ports
    # and a few model-derived points, from the Marine Institute's keyless ERDDAP server
    # (dataset IMI_TidePrediction_HighLow).
    #
    # How often we fetch:
    # - station list (ERDDAP distinct() of the dataset's stations): once per quarter (WebCalTides
    #   caches it to disk); if it fails, again after WebCalTides::TIDE_STATIONS_RETRY (1h)
    # - high/low predictions: one request per station per requested month, covering the whole
    #   13-month window (WebCalTides caches each successful result to disk); after an error (HTTP
    #   status, network failure or unusable response) or an empty window, not again for
    #   UNAVAILABLE_RETRY (1h, in memory, per process)
    #
    # Terms (dataset metadata on ERDDAP, data.marine.ie record ie.marine.data:dataset.2776 and
    # data.gov.ie): licensed under CC BY 4.0, which asks for credit ("Data supplied by Marine
    # Institute"), a link to the licence and a note of what we changed (CHANGES).  MI accepts no
    # responsibility for errors or for any use, and its predictions leave out storm surge.
    #
    # Times: ERDDAP gives UTC (the CSV units row says "UTC" and every timestamp ends in Z), at
    # 5-minute resolution; we keep them as they are.
    #
    # Heights: the high/low dataset gives metres above OD Malin (the Irish land-survey datum; its
    # variable says sea_level_datum = "OD Malin", although the dataset summary says LAT).  Chart
    # users expect chart datum (Lowest Astronomical Tide), so we add a fixed per-station offset
    # (STATIONS below) and round to the centimetre, as MI gives its own LAT heights.
    class MarineInstituteTides < Base

        ERDDAP_URL  = 'https://erddap.marine.ie/erddap/tabledap/IMI_TidePrediction_HighLow'
        HOME_URL    = 'https://www.marine.ie/site-area/data-services/real-time-observations/tidal-predictions'
        LICENSE_URL = 'https://creativecommons.org/licenses/by/4.0/'

        # MI's credit line ("Data supplied by Marine Institute"), with the links and licence
        ATTRIBUTION = "Data supplied by Marine Institute (Ireland), #{HOME_URL}, licensed under CC BY 4.0 (#{LICENSE_URL})"

        # CC BY: indicate changes
        CHANGES = "Changes: heights converted from metres above OD Malin to metres above chart datum (Lowest Astronomical Tide) by adding a fixed offset for each station, taken from MI's own IMI-TidePrediction dataset (Water_Level minus Water_Level_ODM), rounded to 0.01 m, and shown in metres or converted to feet; high and low waters presented as calendar events; times unchanged, in UTC as MI publishes them."

        # The same, for a feed without heights. The note must hold whatever the reason they were left
        # out: no known offset for the station, a height unit other than metres, or no usable value.
        CHANGES_WITHOUT_HEIGHTS = "Changes: heights left out (MI's heights for this station could not be converted to chart datum); high and low waters presented as calendar events; times unchanged, in UTC as MI publishes them."

        DISCLAIMER = "NOT FOR NAVIGATION. Tide predictions of the Marine Institute; MI accepts no responsibility for errors or for their use. Storm surge (atmospheric pressure and wind) is not included."

        include TimeWindow

        def initialize(logger)
            super
            @unavailable       = {}
            @unavailable_mutex = Mutex.new
        end

        # Get a full year (1 month behind + now + 11 ahead).  MI publishes about two years ahead
        # (2026-01-01 to 2028-12-31 on 6 Oct 2026).
        self.window_size = 13.months

        # How long to wait before asking ERDDAP again for a station that errored or had no data in
        # a window.  A live server, so failures are likely transient; an hour still keeps a broken
        # station to one request per hour.
        UNAVAILABLE_RETRY = 1.hour

        TIDE_TYPES = { 'HIGH' => 'High', 'LOW' => 'Low' }.freeze

        # Transport failures get_url can raise (after its retries, for timeouts): no usable response,
        # as opposed to an HTTP error status (Mechanize::ResponseCodeError, rescued before these).
        # Mechanize::Error covers a truncated or cut-short body (ResponseReadError,
        # ChunkedTerminationError), an undecodable content-encoding and too many redirects.
        NETWORK_ERRORS = [
            Timeout::Error, SocketError, SystemCallError, OpenSSL::SSL::SSLError, EOFError, IOError,
            Net::HTTPBadResponse, Net::HTTP::Persistent::Error, Mechanize::Error
        ].freeze

        # What ERDDAP's 404 body says when the query is fine but has no rows (as opposed to, e.g.,
        # "Currently unknown datasetID=...")
        NO_MATCHING_RESULTS = 'Your query produced no matching results'

        STATION_COLUMNS = 'stationID,longitude,latitude'
        DATA_COLUMNS    = 'time,stationID,tide_time_category,Water_Level_ODMalin'

        # ERDDAP station ids are plain words joined by underscores ("Dublin_Port")
        STATION_CODE = /\A[A-Za-z0-9]+(_[A-Za-z0-9]+)*\z/
        TIMESTAMP    = /\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\z/

        # Per ERDDAP station: county (for the location and for searches like "Cork"; nil where the
        # station lies on a county border), and the offset from OD Malin to chart datum (LAT), in
        # metres: LAT height = OD Malin height + offset.
        #
        # Offsets: the mean of Water_Level (vs LAT) minus Water_Level_ODM over every 5-minute value
        # of 7 Oct 2026 and 15 Jun 2028 in MI's IMI-TidePrediction dataset (576 values per station;
        # both columns are rounded to 0.01 m there, so a single value can be 0.01 off).  The two
        # days agree to within 0.001 m at every station, and spot values from Jan 2026 to Dec 2028
        # agree to within the 0.01 m rounding.  IMI-TidePrediction names the model-derived points
        # with a "_MODELLED" suffix (Achill_Island_MODELLED); the high/low dataset doesn't.
        STATIONS = {
            "Achill_Island"     => ["Mayo",      2.430],
            "Aranmore"          => ["Donegal",   2.190],
            "Arklow"            => ["Wicklow",   0.891],
            "Ballycotton"       => ["Cork",      2.460],
            "Ballyglass"        => ["Mayo",      2.109],
            "Bray_Harbour"      => ["Wicklow",   2.190],
            "Buncranna"         => ["Donegal",   2.326],
            "Carrigaholt"       => ["Clare",     2.710],
            "Castletownbere"    => ["Cork",      2.075],
            "Clare_Island"      => ["Mayo",      2.510],
            "Crosshaven"        => ["Cork",      2.060],
            "Dingle"            => ["Kerry",     2.423],
            "Dublin_Port"       => ["Dublin",    2.458],
            "Dungarvan"         => ["Waterford", 2.150],
            "Dunmore"           => ["Waterford", 2.541],
            "Fenit"             => ["Kerry",     2.677],
            "Galway"            => ["Galway",    2.949],
            "Howth"             => ["Dublin",    2.561],
            "Inishmore"         => ["Galway",    2.789],
            "Killary_Harbour"   => [nil,         2.510],
            "Killybegs"         => ["Donegal",   2.267],
            "Kilrush"           => ["Clare",     3.003],
            "Kinsale"           => ["Cork",      2.010],
            "Lahinch"           => ["Clare",     2.680],
            "Letterfrack"       => ["Galway",    2.510],
            "Malin_Head"        => ["Donegal",   2.084],
            "Port_Oriel"        => ["Louth",     2.935],
            "Ringaskiddy"       => ["Cork",      2.430],
            "Roonagh"           => ["Mayo",      1.948],
            "Rossaveel"         => ["Galway",    2.536],
            "Rosslare"          => ["Wexford",   1.080],
            "Skerries"          => ["Dublin",    2.883],
            "Sligo"             => ["Sligo",     2.252],
            "Tom_Clarke_Bridge" => ["Dublin",    2.339],
            "Tory_Island"       => ["Donegal",   2.300],
            "Union_Hall"        => ["Cork",      2.075],
            "Wexford"           => ["Wexford",   0.960],
            "Wicklow"           => ["Wicklow",   1.610]
        }.freeze

        # Common spellings that differ from the ERDDAP id
        OTHER_NAMES = {
            "Buncranna" => ["Buncrana"],
            "Dunmore"   => ["Dunmore East"]
        }.freeze

        # Feed-level credit + what we changed + disclaimer
        def self.feed_description(tides = nil)
            changes = tides.present? && tides.all? { |td| td.prediction.nil? } ? CHANGES_WITHOUT_HEIGHTS : CHANGES
            "#{ATTRIBUTION}. #{changes} #{DISCLAIMER}"
        end

        # Per-event credit
        def self.event_description(_tide = nil)
            "Data supplied by Marine Institute, CC BY 4.0 (#{LICENSE_URL}). NOT FOR NAVIGATION."
        end

        # Station id: 'IE__' + MI's ERDDAP station id, e.g. Dublin Port (Dublin_Port) -> IE__Dublin_Port
        def self.station_id_for(code)
            "IE__#{code}"
        end

        # Other spellings to search by: plain-ASCII (no fadas), common spellings, the county, and
        # TICON's "<name>, IRL" form, so "Dublin Port IRL" finds MI too
        def self.alternate_names(code, name)
            county = STATIONS.dig(code, 0)
            names  = [ActiveSupport::Inflector.transliterate(name)] + OTHER_NAMES.fetch(code, [])
            names << "#{name}, Co. #{county}" if county
            names << "#{name}, IRL"
            names.uniq - [name]
        end

        def tide_stations
            url = "#{ERDDAP_URL}.csv?#{STATION_COLUMNS}&distinct()"

            logger.info "getting tide station list from #{url}"

            unless csv = get_url(url)
                logger.error "!! got no Marine Institute tide station list from #{url}"
                return nil
            end

            # nil (not []) when the list is missing, unreadable, empty or has no usable row, so the
            # caller doesn't cache "no Irish stations" as healthy.  Rows are validated one by one
            # below; a bad row is skipped, not the whole list.
            logger.debug "parsing tide station list from #{ERDDAP_URL}"
            header, _units, *rows = lines(csv)
            if header != STATION_COLUMNS
                logger.error "!! Marine Institute tide station list at #{url} unusable (unexpected header), body starts #{csv[0, 100].inspect}"
                return nil
            end

            if rows.empty?
                logger.error "!! Marine Institute tide station list at #{url} has no stations, body starts #{csv[0, 100].inspect}"
                return nil
            end

            stations = rows.filter_map do |row|
                code, lon, lat = row.split(',', -1).map(&:strip)
                lat, lon = Float(lat, exception: false), Float(lon, exception: false)

                unless code.to_s.match?(STATION_CODE) && lat && lon
                    logger.warn "skipping Marine Institute station without id or position: #{row[0, 200].inspect}"
                    next
                end

                name   = code.tr('_', ' ')
                county = STATIONS.dig(code, 0)
                logger.warn "no chart datum offset for Marine Institute station #{code}, its heights will be left out" unless STATIONS.key?(code)

                Models::Station.new(
                    name: name,
                    alternate_names: self.class.alternate_names(code, name),
                    id: self.class.station_id_for(code),
                    public_id: code,
                    region: 'Ireland',
                    location: [name, county && "Co. #{county}", 'Ireland'].compact.join(", "),
                    lat: lat,
                    lon: lon,
                    url: HOME_URL,
                    provider: 'imi'
                )
            end

            if stations.empty?
                logger.error "!! Marine Institute tide station list at #{url} has no usable stations, body starts #{csv[0, 100].inspect}"
                return nil
            end

            return stations
        end

        def tide_data_for(station, around)
            from = beginning_of_window(around)
            to   = end_of_window(around)

            # A station-level failure (error, unusable response) blocks every window; an empty
            # window only this one
            window_key = "#{station.id}@#{around.utc.strftime('%Y%m')}"
            if (until_time = unavailable_until(station.id) || unavailable_until(window_key))
                logger.debug "skipping Marine Institute tide data for #{station.id}: unavailable, next try after #{until_time}"
                return nil
            end

            code = station.public_id.to_s
            unless code.match?(STATION_CODE)
                logger.error "!! Marine Institute station #{station.id} has an unusable ERDDAP id #{code.inspect}"
                return unavailable!(station.id)
            end

            # ERDDAP wants ", < and > percent-encoded (its front end answers 400 to raw ones)
            url = "#{ERDDAP_URL}.csv?#{DATA_COLUMNS}&stationID=%22#{code}%22" \
                  "&time%3E=#{from.utc.strftime('%Y-%m-%dT%H:%M:%SZ')}&time%3C=#{to.utc.strftime('%Y-%m-%dT%H:%M:%SZ')}"

            logger.info "getting tide data from #{url}"

            begin
                unless csv = get_url(url)
                    logger.error "!! got no Marine Institute tide data for station #{station.id} from #{url}"
                    return unavailable!(station.id)
                end
            rescue Mechanize::ResponseCodeError => e
                # ERDDAP answers 404 for a query with no matching rows (a window past the published
                # horizon, or a station MI dropped): no data for this window, not an error.  It also
                # answers 404 for a dataset it doesn't know (renamed or removed), which is an error.
                body = error_message(e)
                if e.response_code == "404" && body.include?(NO_MATCHING_RESULTS)
                    logger.warn "404 (no matching rows) from Marine Institute for station #{station.id} between #{from.strftime('%Y-%m-%d')} and #{to.strftime('%Y-%m-%d')}, next try in #{UNAVAILABLE_RETRY.inspect}"
                    return unavailable!(window_key)
                end
                # Back off for any other HTTP error, so an outage doesn't turn every feed request into another call
                logger.error "!! Marine Institute tide data for station #{station.id} failed with HTTP #{e.response_code} (#{body[0, 200].inspect}), next try in #{UNAVAILABLE_RETRY.inspect}"
                unavailable!(station.id)
                raise
            rescue *NETWORK_ERRORS => e
                # get_url has already retried timeouts; back off so an outage costs one call per
                # station per UNAVAILABLE_RETRY instead of a full retry sequence on every feed request
                logger.error "!! Marine Institute tide data for station #{station.id} unreachable (#{e.class}: #{e.message}), next try in #{UNAVAILABLE_RETRY.inspect}"
                unavailable!(station.id)
                raise
            end

            logger.debug "parsing tide predictions for #{station.id} from #{ERDDAP_URL}"
            header, units, *rows = lines(csv)
            time_unit, _, _, height_unit = units.to_s.split(',', -1)
            if header != DATA_COLUMNS || time_unit != 'UTC'
                logger.error "!! Marine Institute tide data for station #{station.id} at #{url} unusable (unexpected header or time unit), body starts #{csv[0, 100].inspect}"
                return unavailable!(station.id)
            end

            offset  = STATIONS.dig(code, 1)
            heights = offset && height_unit == 'metres'
            unless heights
                logger.warn "omitting heights of Marine Institute data for station #{station.id}: #{offset ? "unexpected unit #{height_unit.inspect}" : 'no chart datum offset'}"
            end

            skipped   = []
            no_height = 0
            tides = rows.filter_map do |row|
                time, id, category, odm = row.split(',', -1).map(&:strip)

                if id != code
                    logger.error "!! Marine Institute tide data for station #{station.id} at #{url} has rows for #{id.inspect}"
                    return unavailable!(station.id)
                end

                type   = TIDE_TYPES[category]
                time   = parse_timestamp(time) if type
                height = Float(odm, exception: false) if heights

                unless type && time
                    skipped << row
                    next
                end

                no_height += 1 if heights && height.nil?

                Models::TideData.new(
                    type: type,
                    units: "m",
                    prediction: height ? (height + offset).round(2) : nil,
                    time: time,
                    url: station.url
                )
            end

            if skipped.any?
                logger.warn "skipping #{skipped.length} Marine Institute events for station #{station.id} with unknown type or bad timestamp, e.g. #{skipped.first[0, 200].inspect}"
            end

            if no_height > 0
                logger.warn "keeping #{no_height} Marine Institute events for station #{station.id} without a usable height"
            end

            tides = tides.select { |td| td.time >= from && td.time <= to }.sort_by(&:time)

            if tides.empty?
                logger.error "!! no Marine Institute tide data for station #{station.id} between #{from.strftime('%Y-%m-%d')} and #{to.strftime('%Y-%m-%d')}"
                return unavailable!(window_key)
            end

            return tides
        end

        private

        # Non-blank lines, without line endings.  Mechanize hands us the body as binary.  ERDDAP
        # declares text/csv;charset=ISO-8859-1, but the columns we request (station ids, times,
        # categories, numbers) are plain ASCII, which reads the same as UTF-8; scrub guards the rest.
        def lines(csv)
            csv.to_s.dup.force_encoding(Encoding::UTF_8).scrub.each_line.map(&:strip).reject(&:empty?)
        end

        # The body of an HTTP error response (ERDDAP's "Error { ... message=... }"), "" if none
        def error_message(error)
            lines(error.page.respond_to?(:body) ? error.page.body : nil).join(' ')
        end

        # The published UTC instant.  nil if unusable.
        def parse_timestamp(timestamp)
            return nil unless timestamp.to_s.match?(TIMESTAMP)
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

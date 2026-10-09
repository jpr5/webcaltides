##
## Primary library of functions.  Included on Server.
##
#

require 'dotenv/load'

require 'bundler/setup'
Bundler.require(:default, ENV['RACK_ENV'] || 'development')

require 'digest'

require_relative 'lib/gps'
require_relative 'clients/base'
require_relative 'clients/noaa_tides'
require_relative 'clients/chs_tides'
require_relative 'clients/bsh_tides'
require_relative 'clients/kartverket_tides'
require_relative 'clients/linz_tides'
require_relative 'clients/marine_institute_tides'
require_relative 'clients/rijkswaterstaat_tides'
require_relative 'clients/noaa_currents'
require_relative 'clients/harmonics'
require_relative 'clients/lunar'


module WebCalTides

    extend self

    include Clients::TimeWindow

    # Thread-safety mutexes (eagerly initialized to avoid ||= race conditions)
    @@harmonics_mutex        = Mutex.new
    @@tzcache_mutex          = Mutex.new
    @@tide_stations_mutex    = Mutex.new
    @@retired_tide_stations_mutex = Mutex.new
    @@current_stations_mutex = Mutex.new
    @@lunar_phases_mutex     = Mutex.new
    @@cleanup_mutex          = Mutex.new

    # Tracks the month stamp of the last cache cleanup run
    @@last_cleanup_stamp = nil

    # Configuration constants
    STATION_GROUPING_DISTANCE_M = 200  # Meters threshold for grouping nearby stations

    # Priority order for selecting primary source when multiple providers cover the same location.
    # Agency sources (NOAA, CHS, BSH, Kartverket, LINZ, Marine Institute, Rijkswaterstaat) are preferred over
    # harmonic-based predictions.  NOAA and CHS do have nearby stations along the US/Canada border,
    # so their order matters there: NOAA wins (pinned by spec/integration/station_grouping_spec.rb).
    # Kartverket (Norway), LINZ (New Zealand) and the Marine Institute (Ireland) overlap none of the
    # other agency sources within the grouping distance, so their positions among them have no
    # effect.  BSH (Germany) and Rijkswaterstaat (Netherlands) do overlap on the Ems-Dollard border:
    # RWS NL__dukegat and NL__knock are at the same coordinates (0 m) as BSH DE__799G (Dukegat) and
    # DE__802P (Knock, Ems), so each pair is grouped and BSH is the primary there because bsh comes
    # before rws below (pinned by spec/integration/station_grouping_spec.rb).  RWS NL__pogum is 351 m
    # from BSH DE__803P (Pogum, Ems), beyond the grouping distance, so those two stay separate.
    #
    # BSH vs TICON (Oct 2026, issue #49): TICON's timing for German river gauges is materially
    # off vs BSH's official HW/NW tables, which BSH publishes to the minute (issue #49 shows Cranz
    # high waters off by almost an hour).  So BSH wins wherever it covers a gauge -- including gauges where BSH publishes
    # times only (no heights; a sizeable minority of gauges).  For those the primary shows HW/NW
    # times without heights, and a co-located XTide/TICON station with heights is offered only
    # as an alternative.  Ranking is by provider alone and never inspects predictions; this is
    # pinned by spec/integration/station_grouping_spec.rb.
    #
    # Kartverket vs TICON (Oct 2026): TICON's Norwegian stations sit on Kartverket's own gauges
    # (often at identical coordinates), but their high/low times differ from Kartverket's official
    # predictions.  Over November 2026 (116 events per gauge, app feed vs Kartverket's own
    # high/low list), the largest difference was 29 min at Bergen, 46 min at Tromsø and 73 min at
    # Stavanger, and no TICON event matched to the minute.
    # So Kartverket wins wherever it covers a gauge, and TICON remains an alternative.
    #
    # LINZ vs TICON (Oct 2026): the 12 TICON New Zealand stations are within 2 km of LINZ standard
    # ports, and LINZ publishes the port predictions itself.  Over September 2026 (116 events per
    # port, app feed vs the LINZ CSV), TICON's largest difference was 17 min at Auckland, 16 min at
    # Tauranga and 8 min at Lyttelton, with at most one event matching to the minute.  So LINZ wins
    # where the two are grouped, and TICON remains an alternative: at 7 of the 12 (Auckland,
    # Lyttelton, Marsden Point, Napier, Taranaki, Tauranga, Wellington; 24-42 m apart).  The other
    # 5 (Chatham, Wanganui, Jackson Bay, Gisborne, Westport) are 0.5-1.9 km from the LINZ port,
    # beyond STATION_GROUPING_DISTANCE_M, so search shows the LINZ and TICON stations as separate
    # cards there.
    #
    # Marine Institute vs TICON (Oct 2026): 25 TICON Irish stations are within 2 km of MI stations
    # (most at the same coordinates), and MI publishes the predictions itself.  Over October 2026
    # (120 events per station, app feed vs MI's ERDDAP high/low times), TICON's largest difference
    # was 54 min at Dublin Port, 44 min at Ringaskiddy and Howth, 37 min at Skerries, 32 min at
    # Killybegs and 26 min at Galway, with no event matching to the minute.  So MI wins where the
    # two are grouped, and TICON remains an alternative: at 21 of the 25 (0-194 m apart).  The other
    # 4 (Inishmore, Malin Head, River Tolka, the second Sligo) are 0.5-1.4 km from the MI station,
    # beyond STATION_GROUPING_DISTANCE_M, so search shows them as separate cards there.
    #
    # Rijkswaterstaat vs TICON (Oct 2026): 73 TICON Dutch stations are within 2 km of RWS gauges, and
    # RWS publishes the astronomical predictions itself.  Over October 2026 (119-120 events per
    # station, app feed vs RWS's own high/low list), TICON's largest difference was 46 min at
    # Stavenisse, 64 min at Vlissingen, 111 min at Harlingen, 152 min at Den Helder and 171 min at
    # Hoek van Holland, with no event matching to the minute.  So RWS wins where it groups with a
    # TICON station (Den Helder, Harlingen, Hoek van Holland), and TICON remains an alternative;
    # where the two are further apart than the grouping distance (Stavenisse and Vlissingen, about
    # 300 m) they are separate results, RWS listed first.  RWS heights are above NAP, not chart datum.
    #
    # XTide vs TICON (Jan 2026, scripts/compare_harmonic_sources.rb):
    # - Tides: Same timing RMS (~4min), but XTide height RMS 1.56ft vs TICON 3.49ft (2.2x better)
    # - Currents: TICON has no coverage in US waters; XTide is the only harmonic option
    PROVIDER_HIERARCHY = %w[noaa chs bsh kartverket linz imi rws xtide ticon].freeze

    # Stations whose predictions are known to be wrong.  A demoted station is never the primary of
    # a group, and search lists its group after the others.  It stays in the results and its feed
    # still works, but its card, its entry in the source picker and its feed say what is wrong and
    # name the better station (:better).  check_demoted_stations warns at startup about an id that
    # is not in the loaded stations (TICON ids come from coordinates, so a data update can change
    # them).
    DEMOTED_STATIONS = {
        # Vigo, ESP (Oct 2026): TICON's T6e11ade (42.233, -8.733) and Tdce40e9 (42.238, -8.730) are
        # 0.6 km apart, so search shows two cards.  T6e11ade has the TICON-3 constants of the UHSLC
        # Vigo record (vigo-208a-esp-uhslc_rq, 1943-1990; TICON-3 labels it gesla.ispra), whose
        # timestamps are 1 h early: against the IEO Vigo observations (vigo-vigo-esp-ieo) the record
        # lags -60.0 min in 99% of 570 months.  Tdce40e9 has the TICON-3 constants of the CMEMS
        # VigoTG record (gesla.usgs row at 42.243, 351.274; 1992-2021), placed at the IEO gauge's
        # position by scripts/build_ticon_dataset.rb.  T6e11ade minus Tdce40e9: phases M2 -60.3,
        # S2 -62.3, K1 -58.1, O1 -60.8 min; high and low waters over November 2026 median -60.5 min
        # (-61.6 to -58.1, 115 events).  T6e11ade minus the IEO TICON-3 row (gesla.noaa, 42.238,
        # 351.270; 1943-2015): M2 -61.7, S2 -62.7, K1 -62.8, O1 -63.4 min.
        'T6e11ade' => {
            type:     :tide,
            better:   'Tdce40e9',
            warning:  'Times at this station may be about 1 h early',
            evidence: 'Built from the UHSLC Vigo record (1943-1990), whose timestamps are 1 h early (-60.0 min ' \
                      'against IEO Vigo in 99% of 570 months).  Its high and low waters come a median 60.5 min ' \
                      'before those of Tdce40e9 (CMEMS VigoTG, 1992-2021) in November 2026.'
        }.freeze
    }.freeze

    # Timezone fallback mappings for offshore stations where GeoNames returns nil
    US_STATE_TIMEZONES = {
        # Pacific
        'WA' => 'America/Los_Angeles', 'OR' => 'America/Los_Angeles',
        'CA' => 'America/Los_Angeles', 'NV' => 'America/Los_Angeles',
        # Mountain
        'AZ' => 'America/Phoenix', 'MT' => 'America/Denver',
        'ID' => 'America/Boise', 'WY' => 'America/Denver',
        'CO' => 'America/Denver', 'NM' => 'America/Denver', 'UT' => 'America/Denver',
        # Central
        'TX' => 'America/Chicago', 'OK' => 'America/Chicago',
        'KS' => 'America/Chicago', 'NE' => 'America/Chicago',
        'SD' => 'America/Chicago', 'ND' => 'America/Chicago',
        'MN' => 'America/Chicago', 'IA' => 'America/Chicago',
        'MO' => 'America/Chicago', 'AR' => 'America/Chicago',
        'LA' => 'America/Chicago', 'WI' => 'America/Chicago',
        'IL' => 'America/Chicago', 'MS' => 'America/Chicago', 'AL' => 'America/Chicago',
        # Eastern
        'FL' => 'America/New_York', 'GA' => 'America/New_York',
        'SC' => 'America/New_York', 'NC' => 'America/New_York',
        'VA' => 'America/New_York', 'MD' => 'America/New_York',
        'DE' => 'America/New_York', 'NJ' => 'America/New_York',
        'PA' => 'America/New_York', 'NY' => 'America/New_York',
        'CT' => 'America/New_York', 'RI' => 'America/New_York',
        'MA' => 'America/New_York', 'NH' => 'America/New_York',
        'VT' => 'America/New_York', 'ME' => 'America/New_York',
        'OH' => 'America/New_York', 'MI' => 'America/Detroit',
        'IN' => 'America/Indiana/Indianapolis', 'KY' => 'America/Kentucky/Louisville',
        'TN' => 'America/Chicago', 'WV' => 'America/New_York',
        # Other
        'AK' => 'America/Anchorage', 'HI' => 'Pacific/Honolulu',
        'PR' => 'America/Puerto_Rico', 'VI' => 'America/Virgin',
        'GU' => 'Pacific/Guam', 'AS' => 'Pacific/Pago_Pago'
    }.freeze

    CANADA_REGION_TIMEZONES = {
        'Pacific Canada' => 'America/Vancouver',
        'Atlantic Canada' => 'America/Halifax',
        'Northern Canada' => 'America/Yellowknife',
        "Hudson's Bay, Canada" => 'America/Winnipeg',
        'Canada' => 'America/Toronto'
    }.freeze

    REGION_KEYWORDS = {
        /hawaii/i => 'Pacific/Honolulu',
        /alaska/i => 'America/Anchorage',
        /pacific.*canada/i => 'America/Vancouver',
        /atlantic.*canada/i => 'America/Halifax',
        /australia.*sydney/i => 'Australia/Sydney',
        /australia.*perth/i => 'Australia/Perth',
        # Svalbard before Norway: Kartverket's Ny-Ålesund gauge is in "Ny-Ålesund, Norway"
        /svalbard|longyearbyen|ny-ålesund/i => 'Arctic/Longyearbyen',
        /norway/i => 'Europe/Oslo',
        # LINZ ports: Chatham Islands and Scott Base before the rest of New Zealand
        /chatham island/i => 'Pacific/Chatham',
        /scott base/i => 'Antarctica/McMurdo',
        /new zealand/i => 'Pacific/Auckland',
        # Marine Institute stations ("Howth, Co. Dublin, Ireland"); not Bermuda's "Ireland Island"
        # or Northern Ireland
        /(?<!northern )\bireland\z/i => 'Europe/Dublin',
        # Rijkswaterstaat gauges (region "Netherlands"); not "Netherlands Antilles" or "Caribbean Netherlands"
        /(?<!caribbean )\bnetherlands\b(?! antilles)/i => 'Europe/Amsterdam',
        # BSH gauges and the Rijkswaterstaat gauges in Belgium and Germany
        /\bbelgium\b/i => 'Europe/Brussels',
        /\bgermany\b/i => 'Europe/Berlin',
        /uk|england|wales|scotland/i => 'Europe/London',
        /japan/i => 'Asia/Tokyo'
    }.freeze

    LONGITUDE_TIMEZONES = {
        -10 => 'Pacific/Honolulu',
        -9  => 'America/Anchorage',
        -8  => 'America/Los_Angeles',
        -7  => 'America/Denver',
        -6  => 'America/Chicago',
        -5  => 'America/New_York',
        -4  => 'America/Halifax',
        -3  => 'America/Sao_Paulo',
        0   => 'Europe/London',
        1   => 'Europe/Paris',
        8   => 'Asia/Shanghai',
        9   => 'Asia/Tokyo',
        10  => 'Australia/Sydney'
    }.freeze

    def settings
        return Server.settings if defined?(Server)
        Struct.new(:cache_dir).new('cache')
    end
    def logger
        return $LOG ||= Logger.new(STDOUT).tap do |log|
            log.formatter = proc { |s, d, _, m| "#{d.strftime("%Y-%m-%d %H:%M:%S")} #{s} #{m}\n" }
        end
    end

    ##
    ## Clients
    ##

    def tide_clients(provider = nil)
        @tide_clients ||= begin
            harmonics = get_harmonics_client
            {
                noaa:  Clients::NoaaTides.new(logger),
                chs:   Clients::ChsTides.new(logger),
                bsh:   Clients::BshTides.new(logger),
                kartverket: Clients::KartverketTides.new(logger),
                linz:  Clients::LinzTides.new(logger),
                imi:   Clients::MarineInstituteTides.new(logger),
                rws:   Clients::RijkswaterstaatTides.new(logger),
                xtide: harmonics,
                ticon: harmonics
            }
        end

        provider ? @tide_clients[provider.to_sym] : @tide_clients
    end

    def current_clients(provider = nil)
        @current_clients ||= begin
            harmonics = get_harmonics_client
            {
                noaa:  Clients::NoaaCurrents.new(logger),
                xtide: harmonics,
                ticon: harmonics
            }
        end

        provider ? @current_clients[provider.to_sym] : @current_clients
    end

    # Thread-safe harmonics client accessor (singleton across all requests)
    def get_harmonics_client
        return @@harmonics if defined?(@@harmonics) && @@harmonics
        @@harmonics_mutex.synchronize do
            @@harmonics ||= Clients::Harmonics.new(logger)
        end
    end

    def lunar_client
        @lunar_client ||= Clients::Lunar.new(logger)
    end

    # Get the harmonics source files checksum for cache versioning.
    # This ensures quarterly station caches are invalidated when XTide/TICON data changes.
    def harmonics_checksum
        tide_clients(:xtide).engine.source_files_checksum
    end

    # Cache-key part for the quarterly tide and current station lists, which hold station records
    # from the harmonics engine: the dataset (harmonics_checksum) and the engine's station record
    # version (Harmonics::Engine::CACHE_VERSION), so a list cached by code that built those records
    # differently is built again on deploy rather than served until the next quarter.
    def harmonics_stations_key
        "#{harmonics_checksum}_hs#{Harmonics::Engine::CACHE_VERSION}"
    end

    # Cache-key suffix for data the harmonics engine serves: the dataset (harmonics_checksum) and
    # the engine version + HARMONICS_NODAL flag, so a change to any of them rebuilds only harmonics
    # caches.  "" for agency stations, whose names stay as they were.  It goes after the _YYYYMM
    # datestamp, which must stay the first "_20dddd" token for the monthly cleanup.
    def harmonics_cache_key(station)
        return "" unless station&.provider.in?(['xtide', 'ticon'])

        "_#{harmonics_checksum}_#{harmonics_engine_key}"
    end

    # Engine version + nodal mode of the running engine, so cache names match the output it makes.
    def harmonics_engine_key
        tide_clients(:xtide).engine.cache_key_component
    end

    ##
    ## Util
    ##

    # Unit names as sources spell them, to the short form used here.  XTide's TCD reports heights
    # in "feet" (or "meters"), not "ft", so without this an XTide height already in feet was
    # multiplied by 3.28084 again for imperial output.
    LENGTH_UNIT_ALIASES = {
        'ft' => 'ft', 'feet' => 'ft', 'foot' => 'ft',
        'm'  => 'm',  'meters' => 'm', 'metres' => 'm', 'meter' => 'm', 'metre' => 'm'
    }.freeze

    def normalize_length_units(units)
        LENGTH_UNIT_ALIASES.fetch(units.to_s.strip.downcase, units)
    end

    def convert_depth_to_correct_units(val, curr_units, desired_units)
        curr_units    = normalize_length_units(curr_units)
        desired_units = normalize_length_units(desired_units)

        if desired_units == curr_units
            val
        elsif desired_units == 'ft' # convert to feet
            (val.to_f * 3.28084).round(3)
        else # convert to meters
            (val.to_f / 3.28084).round(3)
        end
    end

    # Should handle most (mal)formed inputs using georuby gem.
    def parse_gps(str)
        begin
            str = GPS.normalize(str)[:decimal]
        rescue
            # Fallback to our own original implementation
            # Handles decimal (-)X.YYY or deg/min/sec format:
            # Supported deg/min/sec format:
            #     "1°2.3" or "1'2.3" with explicit negative
            #     "1°2.3N" or "1'2.3W" with implicit negative (S+E -> -)

            if str.match(/\d[°']/) # leave out " because that's also for search
                str = str # blindly fix NSEW, no-op if DNE
                    .gsub(/(\d['°])(\s*)/, '\1') # remove any space b/w deg
                    .gsub(/([^\s])\s+([NSEW])/, '\1\2') # remove any space b/w cardinal
                    .gsub(/([^\s]+)[SE]/, '-\1') # if SE exists, remove + convert to -
                    .gsub(/([^\s]+)[NW]/, '\1') # if NW exists, remove + ignore (+)
                    .gsub(/([-]*)(\d+)['°]\s*(\d+)\.(\d+)/) do |m| # Convert to decimal
                        $1 + ($2.to_f + $3.to_f/60 + $4.to_f/3600).to_s
                    end
            end
        end

        # Hopefully in decimal form now... if it passes validation.
        res = str.split(/[, ]+/)

        return nil if res.length != 2 or
                      res.any? { |s| s.scan(/^[\d\.-]+$/).empty? } or
                     !res[0].to_f.between?(-90,90) or
                     !res[1].to_f.between?(-180,180)
        return res
    end

    def timezone_for(lat, long, station = nil)
        lat = lat.to_f
        long = long.to_f

        # The timezone gem requires longitude in -180..180, but TICON uses 0..360.
        while long > 180.0; long -= 360.0; end
        while long < -180.0; long += 360.0; end

        key = "#{lat} #{long}"

        # Thread-safe cache read (class variables for cross-request safety)
        cached = @@tzcache_mutex.synchronize do
            @@tzcache ||= load_tzcache
            @@tzcache[key]
        end

        # Entries cached before ids were canonicalised can hold a legacy alias: replace it.  An
        # id TZInfo doesn't know at all is looked up again.
        if cached
            canonical = canonical_timezone(cached)
            return cached if canonical == cached

            if canonical
                logger.info "replacing cached timezone for #{key}: #{cached} -> #{canonical}"
                return update_tzcache(key, canonical)
            end

            logger.warn "cached timezone #{cached} for #{key} is unknown, looking it up again"
        end

        # External lookup (outside mutex to avoid blocking other threads)
        logger.debug "looking up tz for GPS #{key}"
        tz = nil
        i = 0
        begin
            i += 1
            tz = Timezone.lookup(lat, long)
        rescue Timezone::Error::InvalidZone
            # Use default UTC
        rescue Timezone::Error::GeoNames => e
            logger.error "GeoNames lookup failed for #{key}: #{e.message}"
            if i < 3
                sleep(i)
                retry
            end
        rescue => e
            logger.error "timezone lookup failed for #{key}: #{e.message}"
        end

        # Google returns CLDR ids, some of which are tzdata backward links (Asia/Saigon)
        res = canonical_timezone(tz.name) if tz
        logger.warn "timezone lookup for #{key} returned unknown zone #{tz.name}" if tz && res.nil?

        # Fallback chain when GeoNames returns nil (offshore locations)
        if res.nil?
            res = timezone_fallback(lat, long, station)
            if res != 'UTC'
                logger.info "timezone fallback for #{key}: #{res} (via region/longitude)"
            else
                logger.warn "Timezone.lookup returned nil for #{key}, defaulting to UTC"
            end
        end

        # Update cache thread-safely
        update_tzcache(key, res)
    end

    # Thread-safe timezone cache update with cross-request class-level mutex.
    def update_tzcache(key, value)
        @@tzcache_mutex.synchronize do
            @@tzcache ||= load_tzcache
            @@tzcache[key] = value
            write_tzcache_to_disk
        end
        value
    end

    private

    # The id to store and hand out for a zone: a tzdata backward alias (Asia/Saigon, America/Godthab,
    # Pacific/Truk) becomes the zone it links to (Asia/Ho_Chi_Minh, America/Nuuk, ...).  Ids that
    # zone1970.tab lists, the ids our fallback tables use (some are links, e.g. Europe/Oslo) and UTC
    # are kept.  Returns nil for an id TZInfo doesn't know.
    def canonical_timezone(name)
        zone = TZInfo::Timezone.get(name)
        return name if zone.canonical_identifier == name || kept_timezone_ids.include?(name)

        zone.canonical_identifier
    rescue TZInfo::InvalidTimezoneIdentifier
        nil
    end

    def kept_timezone_ids
        @kept_timezone_ids ||= Set.new(TZInfo::Country.all.flat_map(&:zone_identifiers) + ['UTC'] +
            [US_STATE_TIMEZONES, CANADA_REGION_TIMEZONES, REGION_KEYWORDS, LONGITUDE_TIMEZONES].flat_map(&:values))
    end

    def load_tzcache
        filename = "#{settings.cache_dir}/tzs.json"
        if File.exist?(filename)
            JSON.parse(File.read(filename)) rescue {}
        else
            {}
        end
    end

    def write_tzcache_to_disk
        filename = "#{settings.cache_dir}/tzs.json"
        atomic_write(filename, @@tzcache.to_json)
    rescue => e
        logger.error "failed to write tzcache: #{e.message}"
    end

    # Fallback chain for timezone lookup when GeoNames returns nil (offshore locations)
    def timezone_fallback(lat, long, station)
        return 'UTC' unless station

        # Layer 1: Try region/location string mapping
        if tz = timezone_from_region(station)
            return tz
        end

        # Layer 2: Longitude-based approximation
        timezone_from_longitude(long)
    end

    # Extract timezone from station region/location strings
    def timezone_from_region(station)
        # Check location for US state abbreviation (e.g., "Shell Point, Tampa Bay, FL")
        if station.location =~ /,\s*([A-Z]{2})$/
            state = $1
            return US_STATE_TIMEZONES[state] if US_STATE_TIMEZONES[state]
        end

        # Check region for Canadian regions
        if tz = CANADA_REGION_TIMEZONES[station.region]
            return tz
        end

        # Check region and location for keyword matches
        region_str = "#{station.region} #{station.location}"
        REGION_KEYWORDS.each do |pattern, tz|
            return tz if region_str =~ pattern
        end

        nil
    end

    # Approximate timezone from longitude
    def timezone_from_longitude(lon)
        # Normalize to -180..180
        while lon > 180; lon -= 360; end
        while lon < -180; lon += 360; end

        offset = (lon / 15.0).round
        LONGITUDE_TIMEZONES[offset] || "Etc/GMT#{offset >= 0 ? '-' : '+'}#{offset.abs}"
    end

    public

    def station_ids
        ids = tide_stations.map(&:id) + current_stations.map(&:bid)

        # Add all keys from the XTide engine cache to support aliased/merged IDs
        xtide = tide_clients(:xtide)
        if xtide.respond_to?(:engine)
            ids += xtide.engine.station_cache_ids
        end

        ids.uniq.compact
    end

    ##
    ## Station Grouping & Deduplication
    ##

    # Represents a group of nearby stations from different providers
    StationGroup = Struct.new(:primary, :alternatives, :deltas, keyword_init: true) do
        def has_alternatives?
            alternatives && alternatives.any?
        end

        def to_h
            {
                primary: primary,
                alternatives: alternatives || [],
                deltas: deltas || {}
            }
        end
    end

    # Groups stations by proximity (within STATION_GROUPING_DISTANCE_M meters)
    # Returns array of StationGroup objects with primary and alternatives
    def group_stations_by_proximity(stations, threshold_m: STATION_GROUPING_DISTANCE_M, match_depth: false)
        return [] if stations.nil? || stations.empty?

        groups = []
        threshold_km = threshold_m / 1000.0

        stations.each do |station|
            # Find existing group within threshold distance
            existing_group = groups.find do |group|
                ref_station = group.first
                next false unless ref_station.lat && ref_station.lon && station.lat && station.lon

                # For current stations, also require matching depth
                if match_depth
                    # Normalize depth comparison (both nil, or both same value)
                    ref_depth = ref_station.respond_to?(:depth) ? ref_station.depth : nil
                    sta_depth = station.respond_to?(:depth) ? station.depth : nil
                    next false unless ref_depth == sta_depth
                end

                distance_km = Geocoder::Calculations.distance_between(
                    [ref_station.lat, ref_station.lon],
                    [station.lat, station.lon],
                    units: :km
                )
                distance_km <= threshold_km
            end

            if existing_group
                existing_group << station
            else
                groups << [station]
            end
        end

        # Convert raw groups to StationGroup objects with primary selection
        groups.map { |g| select_primary_and_alternatives(g) }
    end

    # Given a group of stations, selects primary based on provider hierarchy
    # and returns a StationGroup with primary, alternatives, and (empty) deltas
    def select_primary_and_alternatives(group)
        sorted = group.sort_by do |station|
            provider = (station.provider || 'unknown').downcase
            [demoted_station?(station) ? 1 : 0, PROVIDER_HIERARCHY.index(provider) || 999]
        end

        StationGroup.new(
            primary: sorted.first,
            alternatives: sorted[1..] || [],
            deltas: {}  # Populated lazily via compute_variance
        )
    end

    def demoted_station?(station)
        !station.nil? && DEMOTED_STATIONS.key?(station.id)
    end

    # "<warning>.  Use <better station> instead." for a demoted station, nil for any other
    def demotion_warning(station)
        return nil unless demoted_station?(station)

        entry  = DEMOTED_STATIONS[station.id]
        better = demotion_better_stations(entry[:type])[entry[:better]]
        name   = better ? "#{better.name} (#{entry[:better]})" : entry[:better]

        "#{entry[:warning]}.  Use #{name} instead."
    end

    # The station list a DEMOTED_STATIONS entry's ids are in (:type is :tide or :current)
    def demotion_station_list(type)
        case type
        when :tide    then tide_stations
        when :current then current_stations
        else raise ArgumentError, "DEMOTED_STATIONS :type must be :tide or :current, not #{type.inspect}"
        end
    end

    # Bumped by remove_tide_station and remove_current_station, which change a list in place
    def demotion_list_version(type)
        (@demotion_list_versions ||= Hash.new(0))[type]
    end

    # { id => station } for the :better ids of the DEMOTED_STATIONS entries of one type, found in
    # the list of that type only, by station id as demoted_station? matches.  Built once per list,
    # again when the list is replaced, and after remove_tide_station or remove_current_station
    # changes it in place: a table is kept with the list's version from before the build, so one
    # built across a removal is not used after it.  A :better id that is not in the list is logged
    # once per build.
    def demotion_better_stations(type)
        version = demotion_list_version(type)
        list    = demotion_station_list(type)
        built   = (@demotion_better_stations ||= {})[type]
        return built[2] if built && built[0].equal?(list) && built[1] == version

        entries = DEMOTED_STATIONS.select { |_, entry| entry[:type] == type }
        wanted  = entries.values.map { |entry| entry[:better] }.to_set
        found   = {}
        list.each { |s| found[s.id] ||= s if wanted.include?(s.id) }
        entries.each do |id, entry|
            next if found.key?(entry[:better])
            logger.error "!! better station #{entry[:better]} for demoted station #{id} is not in the #{type} station list (#{list.length} stations), so its warning names a missing station"
        end

        @demotion_better_stations[type] = [list, version, found]
        found
    end

    # Cache-key part for a demoted station's feed: a digest of the warning it carries, so a feed
    # cached before the demotion, or before its warning or better station changed, is not served.
    # "" for any other station.
    def demotion_cache_key(station)
        warning = demotion_warning(station) or return ""
        "_demoted#{Digest::MD5.hexdigest(warning)[0, 8]}"
    end

    # Puts the demotion warning at the start of the calendar's description (DESCRIPTION, and
    # X-WR-CALDESC, which Apple and Google Calendar show for a subscribed calendar) and of each
    # event's description.  Call it before solar and lunar events are added, which keep their own
    # descriptions.
    def add_demotion_warning(calendar, station)
        warning = demotion_warning(station) or return calendar

        # A calendar's DESCRIPTION and X-WR-CALDESC hold a list of values, an event's one value
        prepend = ->(desc) { [warning, *Array(desc).map(&:to_s).reject(&:blank?)].join("\n\n") }
        calendar.description = prepend.(calendar.description)
        # Stored under "x-wr-caldesc" when appended, "x_wr_caldesc" when parsed
        keys    = %w[x-wr-caldesc x_wr_caldesc]
        caldesc = prepend.(keys.flat_map { |k| calendar.custom_properties.delete(k) || [] })
        calendar.append_custom_property('X-WR-CALDESC', caldesc)
        calendar.events.each { |e| e.description = prepend.(e.description) }

        calendar
    end

    # Logs an error for every DEMOTED_STATIONS id, and every :better id, that is not a station id
    # in the station list of the entry's type as loaded at startup: its demotion does nothing, or
    # its warning names a station that is not there, while that list is served.  Called at
    # startup, once the station lists are loaded.  Returns the ids.
    def check_demoted_stations
        lists = %i[tide current].to_h { |type| [type, demotion_station_list(type)] }
        ids   = lists.transform_values { |list| list.map(&:id).to_set }
        where = ->(type) { "the #{type} station list loaded at startup (#{lists[type].length} stations)" }

        missing = DEMOTED_STATIONS.reject { |id, entry| ids.fetch(entry[:type]).include?(id) }
        missing.each { |id, entry| logger.error "!! demoted station #{id} is not in #{where.(entry[:type])}, so its demotion does nothing while that list is served" }

        lost = DEMOTED_STATIONS.reject { |_, entry| ids.fetch(entry[:type]).include?(entry[:better]) }
        lost.each { |id, entry| logger.error "!! better station #{entry[:better]} for demoted station #{id} is not in #{where.(entry[:type])}, so its warning names a missing station while that list is served" }

        missing.keys + lost.values.map { |entry| entry[:better] }
    end

    # Computes time and height deltas between primary and each alternative
    # Returns hash: { "alt_station_id" => { time: "+4min", height: "-0.2ft" }, ... }
    def compute_variance(primary, alternatives, around: Time.current.utc)
        return {} if alternatives.nil? || alternatives.empty?

        primary_events = next_tide_events(primary.id, around: around)
        return {} unless primary_events && primary_events.any?

        primary_next = primary_events.first

        alternatives.each_with_object({}) do |alt, deltas|
            alt_events = next_tide_events(alt.id, around: around)
            next unless alt_events && alt_events.any?

            alt_next = alt_events.first

            # Calculate deltas.  A side without a height (times-only sources such as many BSH
            # gauges) has nothing to compare, so the height delta is nil rather than a fake one.
            time_diff_seconds = (alt_next[:time].to_time - primary_next[:time].to_time).to_i
            # Heights above different datums (NAP vs chart datum) aren't comparable either.
            height_delta = unless alt_next[:height].nil? || primary_next[:height].nil? || alt_next[:datum] != primary_next[:datum]
                units       = primary_next[:units] || 'ft'
                alt_height  = convert_depth_to_correct_units(alt_next[:height].to_f, alt_next[:units] || 'ft', units)
                height_diff = (alt_height.to_f - primary_next[:height].to_f).round(2)
                format_height_delta(height_diff, units)
            end

            deltas[alt.id] = {
                time: format_time_delta(time_diff_seconds),
                height: height_delta
            }
        end
    end

    # Formats time delta in seconds to human-readable string
    def format_time_delta(seconds)
        return "0min" if seconds.abs < 30  # Less than 30 seconds = essentially same time

        sign = seconds >= 0 ? "+" : ""
        minutes = (seconds / 60.0).round

        if minutes.abs >= 60
            hours = minutes / 60
            mins = minutes.abs % 60
            mins_str = mins > 0 ? "#{mins}min" : ""
            "#{sign}#{hours}hr#{mins_str}"
        else
            "#{sign}#{minutes}min"
        end
    end

    # Formats height delta with sign and units
    def format_height_delta(diff, units = 'ft')
        return "0#{units}" if diff.abs < 0.05

        sign = diff >= 0 ? "+" : ""
        "#{sign}#{diff}#{units}"
    end

    # Groups search results and optionally computes variance
    # Set compute_deltas: false for faster initial search results
    def group_search_results(stations, compute_deltas: false, match_depth: false, around: Time.current.utc)
        groups = group_stations_by_proximity(stations, match_depth: match_depth)
        # Demoted stations after the others, in their order (see DEMOTED_STATIONS)
        groups = groups.partition { |group| !demoted_station?(group.primary) }.flatten(1)

        if compute_deltas
            groups.each do |group|
                next unless group.has_alternatives?
                group.deltas = compute_variance(group.primary, group.alternatives, around: around)
            end
        end

        groups
    end

    ##
    ## Tides
    ##

    # Cache quarterly / every three months, versioned by the harmonics dataset and station record
    # version (harmonics_stations_key) and the set of tide providers (so adding a provider doesn't wait for the next quarter to show up)
    def tide_station_cache_file
        now = Time.current.utc
        datestamp = now.strftime("%YQ#{now.quarter}")
        # A client's station_list_version (if it has one) is part of it, so a change to which stations a
        # client lists rebuilds the list on deploy rather than next quarter
        providers = Digest::MD5.hexdigest(tide_clients.map { |name, c| [name, c.class.try(:station_list_version)].compact.join(":") }.sort.join(","))[0, 8]
        "#{settings.cache_dir}/tide_stations_v#{Models::Station.version}_#{datestamp}_#{harmonics_stations_key}_#{providers}.json"
    end

    # If a provider's station list fails, serve the others but don't cache the incomplete list for
    # the quarter; build it again after this long.
    TIDE_STATIONS_RETRY = 1.hour

    # Returns [stations, complete]; complete is false if any provider failed (logged).  Each
    # provider is isolated so one upstream outage doesn't take down search for every region.
    def fetch_tide_stations
        complete = true

        stations = tide_clients.values.uniq.flat_map do |c|
            list = c.tide_stations
            raise "no station list" unless list
            raise "empty station list" if list.empty?
            if c.try(:station_list_degraded?)
                # A fallback list (see Clients::ChsTides#tide_stations): serve it, but don't cache it
                logger.error "!! degraded tide station list from #{c.class.name}, serving it uncached"
                complete = false
            end
            list
        rescue => e
            logger.error "!! failed to get tide station list from #{c.class.name}, leaving it out: #{e.class} - #{e.message}"
            complete = false
            []
        end

        return stations, complete
    end

    def cache_tide_stations(at:tide_station_cache_file, stations:[])
        # stations: is used in the re-cache scenario
        if stations.empty?
            logger.error "!! not caching an empty tide station list at #{at}"
            return false
        end

        logger.debug "storing tide station list at #{at}"
        atomic_write(at, stations.map(&:to_h).to_json)

        return stations.length > 0
    end

    def tide_stations
        # Double-checked locking for thread safety
        # First check is optimization - safe because array assignment is atomic in Ruby
        return @tide_stations if @tide_stations && !tide_stations_retry_due?

        @@tide_stations_mutex.synchronize do
            return @tide_stations if @tide_stations && !tide_stations_retry_due?

            cache_file = tide_station_cache_file
            stations   = nil

            # An unreadable cache file is removed and rebuilt once, rather than failing every request
            2.times do
                unless File.exist?(cache_file)
                    # Other requests keep the incomplete list, if there is one, while this rebuilds it
                    @tide_stations_retry_at = Time.current.utc + TIDE_STATIONS_RETRY if @tide_stations
                    stations, complete = fetch_tide_stations
                    unless complete
                        @tide_stations_retry_at = Time.current.utc + TIDE_STATIONS_RETRY
                        # A rebuild that got nothing doesn't replace the list we already have
                        if stations.empty? && @tide_stations.present?
                            logger.warn "tide station rebuild got no stations, keeping the last list (#{@tide_stations.length} stations) uncached, rebuilding after #{@tide_stations_retry_at}"
                            return @tide_stations
                        end
                        logger.warn "serving incomplete tide station list (#{stations.length} stations) uncached, rebuilding after #{@tide_stations_retry_at}"
                        return @tide_stations = stations
                    end

                    cache_tide_stations(at: cache_file, stations: stations)
                end

                loaded = begin
                    logger.debug "reading #{cache_file}"
                    data = JSON.parse(File.read(cache_file))
                    raise TypeError, "expected a station list, got #{data.class}" unless data.is_a?(Array)
                    # An empty list is never cached on purpose; don't serve one for the quarter
                    raise "empty station list" if data.empty?

                    logger.debug "parsing tide station list"
                    data.map { |js| Models::Station.from_hash(js) }
                rescue => e
                    logger.error "!! unreadable tide station cache #{cache_file}, removing it: #{e.class} - #{e.message}"
                    File.unlink(cache_file) rescue nil
                    nil
                end

                if loaded
                    # The degraded list stays in place (and is retried) until the complete one is loaded
                    @tide_stations_retry_at = nil
                    return @tide_stations = loaded
                end
            end

            # Even the rebuilt file couldn't be read back: serve what was fetched, uncached, and retry later
            @tide_stations = stations || @tide_stations || []
            @tide_stations_retry_at = Time.current.utc + TIDE_STATIONS_RETRY
            logger.warn "serving tide station list (#{@tide_stations.length} stations) uncached, rebuilding after #{@tide_stations_retry_at}"
            @tide_stations
        end
    end

    # Incomplete in-memory list (some provider failed) that is due for another build
    def tide_stations_retry_due?
        @tide_stations_retry_at && Time.current.utc >= @tide_stations_retry_at
    end

    # This is primarily for CHS tide stations, whose metadata is such a broken mess as to not
    # reliably indicate, in any way, whether the station is producing tide data or not.  See
    # chs_tides.rb for details.

    def remove_tide_station(station_id)
        # Under the lock, so a rebuild can't swap the list between the removal and the write
        @@tide_stations_mutex.synchronize do
            @tide_stations.delete_if { |s| s.id == station_id }
            # The list changed in place, so a table of better stations built from it is not used again
            (@demotion_list_versions ||= Hash.new(0))[:tide] += 1
            # An incomplete list is never cached; it's rebuilt (with this station) on the next retry
            cache_tide_stations(stations:@tide_stations) unless @tide_stations_retry_at
        end
    end

    RETIRED_TIDE_STATION_SUMMARY = "Station retired by DFO – no tide predictions available"

    def retired_tide_station_cache_file
        now = Time.current.utc
        "#{settings.cache_dir}/retired_tide_stations_v1_#{now.strftime("%YQ#{now.quarter}")}.json"
    end

    # CHS stations left out of the station list because DFO doesn't publish high/low predictions for
    # them (see Clients::ChsTides#tide_stations).  Existing subscriptions to one get a feed saying
    # the station is retired instead of a 404.  { id => name }, cached quarterly like the station
    # lists.  A failed fetch, or a station list the client rejects as unusable, isn't cached: the
    # last good list (if any) is kept and the fetch is tried again after TIDE_STATIONS_RETRY.  The
    # in-memory list belongs to its quarter's cache file and is reloaded when the quarter changes.
    def retired_tide_stations
        current = -> {
            @retired_tide_stations && @retired_tide_stations_file == retired_tide_station_cache_file &&
                !(@retired_tide_stations_retry_at && Time.current.utc >= @retired_tide_stations_retry_at)
        }
        return @retired_tide_stations if current.call

        @@retired_tide_stations_mutex.synchronize do
            return @retired_tide_stations if current.call

            cache_file = retired_tide_station_cache_file
            stations   = begin
                if File.exist?(cache_file)
                    data = JSON.parse(File.read(cache_file))
                    raise TypeError, "expected { id => name }, got #{data.class}" unless data.is_a?(Hash)
                    data
                end
            rescue => e
                logger.error "!! unreadable retired tide station cache #{cache_file}, rebuilding it: #{e.class} - #{e.message}"
                nil
            end

            unless stations
                begin
                    stations = tide_clients(:chs).retired_stations or raise "no station list"
                    raise TypeError, "expected { id => name }, got #{stations.class}" unless stations.is_a?(Hash)
                    atomic_write(cache_file, stations.to_json)
                rescue => e
                    logger.error "!! failed to get retired CHS stations, retrying after #{TIDE_STATIONS_RETRY.inspect}: #{e.class} - #{e.message}"
                    @retired_tide_stations_retry_at = Time.current.utc + TIDE_STATIONS_RETRY
                    @retired_tide_stations_file     = cache_file # the last good list stands in until the retry
                    return @retired_tide_stations ||= {}
                end
            end

            @retired_tide_stations_retry_at = nil
            @retired_tide_stations_file     = cache_file
            @retired_tide_stations = stations
        end
    end

    # Only a CHS-shaped id (24 hex digits) is looked up, so other unknown ids don't load the list
    def retired_tide_station?(id)
        return false unless id.is_a?(String) && id.match?(/\A\h{24}\z/)

        retired_tide_stations.key?(id)
    end

    # One all-day event over the whole month of `month`, saying the station is retired.  The feed
    # is cached per month, so this keeps the notice current whenever a subscriber syncs.
    def retired_tide_calendar_for(id, month: Time.current.utc)
        return nil unless retired_tide_station?(id)

        name  = retired_tide_stations[id].presence || "this station"
        first = month.utc.to_date.beginning_of_month

        cal = Icalendar::Calendar.new
        cal.x_wr_calname = "#{name.titleize} (retired)"

        cal.event do |e|
            e.summary     = RETIRED_TIDE_STATION_SUMMARY
            e.dtstart     = Icalendar::Values::Date.new(first)
            e.dtend       = Icalendar::Values::Date.new(first.next_month) # exclusive: through the last day
            e.description = "Fisheries and Oceans Canada (DFO) no longer publishes tide predictions for #{name}. " \
                            "Go to https://webcaltides.org to choose another station."
            e.url         = "https://webcaltides.org"
        end

        logger.info "retired tide calendar for #{id} (#{name}) generated"

        return cal
    end

    def tide_station_for(id)
        return nil if id.blank?
        station = tide_stations.find { |s| s.id == id }
        return station if station

        # Fallback to looking in the XTide engine cache for aliased/merged IDs
        xtide = tide_clients(:xtide)
        if xtide.respond_to?(:engine)
            if data = xtide.engine.station_data(id, 'tide').presence
                return Models::Station.from_hash({
                    'name' => data['name'],
                    'id' => id,
                    'public_id' => id,
                    'region' => data['region'],
                    'location' => data['name'],
                    'provider' => data['provider'] || 'xtide', # or ticon? engine knows.
                    'type' => data['type']
                })
            end
        end
        nil
    end

    # nil == any, units == [ mi, km ]
    def find_tide_stations(by:nil, within:nil, units:'mi')
        by ||= [""]
        by &&= Array(by).map(&:downcase)

        logger.debug("finding tide stations by #{by} within #{within}#{units}")
        by_stations = tide_stations.select do |s|
            by.all? do |b|
                s.id.downcase == b ||
                s.alternate_names.any? { |n| (n.downcase.include?(b) rescue false) } ||
                (s.region.downcase.include?(b) rescue false) ||
                (s.name.downcase.include?(b) rescue false) ||
                s.public_id.downcase.include?(b) rescue false
            end
        end

        # can only do radius search with one result, ignore otherwise
        return by_stations unless within and by_stations.size == 1

        station = by_stations.first

        return find_tide_stations_by_gps(station.lat, station.lon, within:within, units:units)
    end

    def find_tide_stations_by_gps(lat, long, within:nil, units:'mi')
        within = within.to_i
        return tide_stations.select do |s|
            Geocoder::Calculations.distance_between([lat, long], [s.lat,s.lon], units: units.to_sym) <= within
        end
    end

    # Fetches and caches the station's data for the month.  Returns the data, or false if there is
    # none.
    def cache_tide_data_for(station, at:, around:)
        return false unless station

        tide_data = tide_clients(station.provider).tide_data_for(station, around)

        # Nothing to cache for an empty list either -- it would serve "no tides" for the month
        return false if tide_data.blank?

        # A window the source hasn't published in full yet (Clients::PartialWindow) is served but
        # not cached, or the month would keep it partial after the rest is published
        if partial?(tide_data)
            logger.info "not caching partial tide data for #{station.id} at #{at}"
        else
            logger.debug "storing tide data at #{at}"
            atomic_write(at, tide_data.map(&:to_h).to_json)
        end

        return tide_data
    end

    def partial?(data)
        data.respond_to?(:partial?) && data.partial?
    end

    def tide_data_for(station, around: Time.current.utc)
        return nil unless station

        datestamp = around.utc.strftime("%Y%m")
        filename  = "#{settings.cache_dir}/tides_v#{Models::TideData.version}_#{station.id}_#{datestamp}#{harmonics_cache_key(station)}.json"
        unless File.exist?(filename)
            tide_data = cache_tide_data_for(station, at:filename, around:around) or return nil
            return tide_data if partial?(tide_data)
        end

        logger.debug "reading #{filename}"
        json = File.read(filename)

        logger.debug "parsing tides for #{station.id}"
        data = JSON.parse(json) rescue []

        return data.map{ |js| Models::TideData.from_hash(js) }
    end

    # Returns the next high and low tide events for a station
    # Returns array of hashes: [{ type: 'High', time: DateTime, height: Float, units: String }, ...],
    # plus datum: (e.g. 'NAP') for a source whose heights are not above chart datum
    def next_tide_events(id, around: Time.current.utc)
        station = tide_station_for(id) or return nil
        data = tide_data_for(station, around: around) or return nil

        now = Time.current.utc
        future_data = data.select { |d| d.time > now }.sort_by(&:time)

        next_high = future_data.find { |d| d.type == 'High' }
        next_low = future_data.find { |d| d.type == 'Low' }

        tz = timezone_for(station.lat, station.lon, station)

        events = []
        if next_high
            events << {
                type: 'High',
                time: next_high.time.in_time_zone(tz),
                height: next_high.prediction,
                units: next_high.units
            }
        end
        if next_low
            events << {
                type: 'Low',
                time: next_low.time.in_time_zone(tz),
                height: next_low.prediction,
                units: next_low.units
            }
        end

        # Heights above another datum (Rijkswaterstaat: NAP) say so, and are not compared with chart datum heights
        client = tide_clients(station.provider)
        events.each { |e| e[:datum] = client.class.height_datum } if client.class.respond_to?(:height_datum)

        # Sort by time so first tide is the soonest
        events.sort_by { |e| e[:time] }
    end

    def tide_calendar_for(id, around: Time.current.utc, units: 'imperial')
        depth_units = units == 'imperial' ? 'ft' : 'm'
        station = tide_station_for(id) or return nil
        data    = tide_data_for(station, around: around) or return nil

        cal = Icalendar::Calendar.new
        # Kartverket, LINZ, Marine Institute and Rijkswaterstaat names are already properly cased; titleize would mangle them
        # ("Ny-Ålesund" to "Ny ålesund", "Port Ōhope Wharf" to "Port ōhope Wharf", "Waitangi - Chatham Island" loses its dash,
        # "IJmuiden, buitenhaven" to "I Jmuiden, Buitenhaven", "Hoek van Holland" to "Hoek Van Holland")
        cal.x_wr_calname = station.provider.in?(['kartverket', 'linz', 'imi', 'rws']) ? station.name : station.name.titleize

        if station.provider.in?(['xtide', 'ticon'])
            cal.description = "NOT FOR NAVIGATION. This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  The author and the publisher each assume no liability for damages arising from use of these predictions.  They are not certified to be correct, and they do not incorporate the effects of tropical storms, El Niño, seismic events, subsidence, uplift, or changes in global sea level."
        end

        # BSH, Kartverket and Marine Institute terms require, and LINZ's terms ask for, the source
        # credit in every presentation, so on the feed and every event.  Rijkswaterstaat's CC0
        # doesn't, but the feed must say its heights are above NAP, not chart datum.
        credited = {
            'bsh' => Clients::BshTides, 'kartverket' => Clients::KartverketTides, 'linz' => Clients::LinzTides,
            'imi' => Clients::MarineInstituteTides, 'rws' => Clients::RijkswaterstaatTides
        }[station.provider]
        # A height datum other than chart datum is named after every height ("1.74 m NAP")
        datum = credited.respond_to?(:height_datum) ? " #{credited.height_datum}" : ""
        if credited
            caldesc = credited.feed_description(data)
            cal.description = caldesc
            cal.append_custom_property('X-WR-CALDESC', caldesc)
        end

        if data
            data.each do |tide|
                # Times without heights: BSH gauges that publish times only, Kartverket data with an
                # unexpected datum, unit or height value, LINZ windows with a year file whose units
                # line doesn't say metres, and Marine Institute stations without a chart datum offset
                title = if tide.prediction.nil?
                    "#{tide.type} Tide"
                else
                    "#{tide.type} Tide #{convert_depth_to_correct_units(tide.prediction, tide.units, depth_units)} #{depth_units}#{datum}"
                end

                cal.event do |e|
                    e.summary     = title
                    e.dtstart     = Icalendar::Values::DateTime.new(tide.time, tzid: 'GMT')
                    e.dtend       = Icalendar::Values::DateTime.new(tide.time, tzid: 'GMT')
                    e.url         = tide.url
                    e.location    = station.location
                    e.description = credited.event_description(tide) if credited
                end
            end
        end

        cal.define_singleton_method(:station)  { station }
        cal.define_singleton_method(:location) { station.location }
        # Built from a partial window: the server doesn't cache the feed for the month either
        partial = partial?(data)
        cal.define_singleton_method(:partial?) { partial }

        logger.info "tide calendar for #{station.name} generated with #{cal.events.length} events"

        return cal
    end

    ##
    ## Currents
    ##

    # Cache quarterly / every three months, versioned by the harmonics dataset and station record
    # version (harmonics_stations_key)
    def current_station_cache_file
        now = Time.current.utc
        datestamp = now.strftime("%YQ#{now.quarter}")
        "#{settings.cache_dir}/current_stations_v#{Models::Station.version}_#{datestamp}_#{harmonics_stations_key}.json"
    end

    # Quarterly-versioned region mapping file
    def noaa_current_regions_file
        now = Time.current.utc
        datestamp = now.strftime("%YQ#{now.quarter}")
        "#{settings.cache_dir}/noaa_current_regions_#{datestamp}.json"
    end

    # Build region mapping by finding nearest NOAA tide station for each current station.
    # Uses spatial grid indexing to avoid O(n×m) brute force search (~23x speedup: 46s → ~2s).
    def build_noaa_current_regions(noaa_current_stations)
        noaa_tide_stations = tide_stations.select { |s| s.provider == 'noaa' }
        logger.info "building NOAA current region mapping (#{noaa_current_stations.size} currents, #{noaa_tide_stations.size} tides)"

        # Build spatial grid index for fast nearest-neighbor lookups
        logger.debug "building spatial grid index from #{noaa_tide_stations.size} tide stations"
        start = Time.now

        # Grid with 2-degree cells (approximately 138 miles at equator)
        grid_size = 2.0
        spatial_grid = Hash.new { |h, k| h[k] = [] }

        noaa_tide_stations.each do |ts|
            next unless ts.lat && ts.lon
            # Assign to grid cell based on lat/lon
            cell_lat = (ts.lat / grid_size).floor
            cell_lon = (ts.lon / grid_size).floor
            spatial_grid[[cell_lat, cell_lon]] << ts
        end

        elapsed = (Time.now - start).round(2)
        logger.debug "spatial grid built in #{elapsed}s (#{spatial_grid.size} cells)"

        # Query grid for each current station to find nearest tide station
        region_map = {}
        noaa_current_stations.each_with_index do |cs, idx|
            next unless cs.lat && cs.lon

            # Find grid cell and neighboring cells
            cell_lat = (cs.lat / grid_size).floor
            cell_lon = (cs.lon / grid_size).floor

            # Check 3x3 grid around current station (9 cells)
            candidates = []
            (-1..1).each do |dlat|
                (-1..1).each do |dlon|
                    candidates.concat(spatial_grid[[cell_lat + dlat, cell_lon + dlon]] || [])
                end
            end

            # If no candidates in 3x3 grid, expand to 5x5 (for Alaska/Hawaii/sparse areas)
            if candidates.empty?
                (-2..2).each do |dlat|
                    (-2..2).each do |dlon|
                        candidates.concat(spatial_grid[[cell_lat + dlat, cell_lon + dlon]] || [])
                    end
                end
            end

            # Find closest candidate using actual distance
            closest = candidates.min_by do |ts|
                Geocoder::Calculations.distance_between([cs.lat, cs.lon], [ts.lat, ts.lon])
            end

            region_map[cs.id] = closest.region if closest&.region && closest.region != 'United States'

            # Progress logging every 1000 stations
            if (idx + 1) % 1000 == 0
                logger.info "  processed #{idx + 1}/#{noaa_current_stations.size} stations"
            end
        end

        # Save for future use within this quarter
        regions_file = noaa_current_regions_file
        logger.info "saving region mapping to #{regions_file} (#{region_map.size} mappings)"
        atomic_write(regions_file, JSON.generate({
            'generated_at' => Time.now.utc.iso8601,
            'regions' => region_map
        }))

        region_map
    end

    # If a provider's current station list fails, serve the others but don't cache the incomplete
    # list for the quarter; build it again after this long.
    CURRENT_STATIONS_RETRY = 1.hour

    # Returns [stations, complete]; complete is false if any provider failed (logged).  Each
    # provider is isolated so one upstream outage doesn't take down search for every region.
    def fetch_current_stations
        complete = true

        stations = current_clients.values.uniq.flat_map do |c|
            list = c.current_stations
            raise "no station list" unless list
            raise "empty station list" if list.empty?
            list
        rescue => e
            logger.error "!! failed to get current station list from #{c.class.name}, leaving it out: #{e.class} - #{e.message}"
            complete = false
            []
        end

        return stations, complete
    end

    def enrich_current_stations(stations)
        # Enrich NOAA current stations with region data
        # (NOAA currents API doesn't provide state/region info, but tide stations do)
        noaa_current_stations = stations.select { |s| s.provider == 'noaa' && s.region == 'United States' }
        if noaa_current_stations.any?
            regions_file = noaa_current_regions_file

            # Load existing mapping or build new one (quarterly refresh)
            if File.exist?(regions_file)
                region_data = JSON.parse(File.read(regions_file)) rescue {}
                region_map = region_data['regions'] || {}
                logger.info "enriching #{noaa_current_stations.size} NOAA current stations from cached regions (#{region_map.size} mappings)"
            else
                region_map = build_noaa_current_regions(noaa_current_stations)
            end

            noaa_current_stations.each do |cs|
                cs.region = region_map[cs.id] if region_map[cs.id]
            end
        end

        stations
    end

    def cache_current_stations(at:current_station_cache_file, stations: [])
        if stations.empty?
            logger.error "!! not caching an empty current station list at #{at}"
            return false
        end

        enrich_current_stations(stations)

        logger.debug "storing current station list at #{at}"
        atomic_write(at, stations.map(&:to_h).to_json)

        return stations.length > 0
    end

    def current_stations
        # Double-checked locking for thread safety
        # First check is optimization - safe because array assignment is atomic in Ruby
        return @current_stations if @current_stations && !current_stations_retry_due?

        @@current_stations_mutex.synchronize do
            return @current_stations if @current_stations && !current_stations_retry_due?

            cache_file = current_station_cache_file
            stations   = nil

            # An unreadable cache file is removed and rebuilt once, rather than failing every request
            2.times do
                unless File.exist?(cache_file)
                    # Other requests keep the incomplete list, if there is one, while this rebuilds it
                    @current_stations_retry_at = Time.current.utc + CURRENT_STATIONS_RETRY if @current_stations
                    stations, complete = fetch_current_stations
                    unless complete
                        @current_stations_retry_at = Time.current.utc + CURRENT_STATIONS_RETRY
                        # A rebuild that got nothing doesn't replace the list we already have
                        if stations.empty? && @current_stations.present?
                            logger.warn "current station rebuild got no stations, keeping the last list (#{@current_stations.length} stations) uncached, rebuilding after #{@current_stations_retry_at}"
                            return @current_stations
                        end
                        logger.warn "serving incomplete current station list (#{stations.length} stations) uncached, rebuilding after #{@current_stations_retry_at}"
                        return @current_stations = enrich_current_stations(stations)
                    end

                    cache_current_stations(at: cache_file, stations: stations)
                end

                loaded = begin
                    logger.debug "reading #{cache_file}"
                    data = JSON.parse(File.read(cache_file))
                    raise TypeError, "expected a station list, got #{data.class}" unless data.is_a?(Array)
                    # An empty list is never cached on purpose; don't serve one for the quarter
                    raise "empty station list" if data.empty?

                    logger.debug "parsing current station list"
                    data.map { |js| Models::Station.from_hash(js) }
                rescue => e
                    logger.error "!! unreadable current station cache #{cache_file}, removing it: #{e.class} - #{e.message}"
                    File.unlink(cache_file) rescue nil
                    nil
                end

                if loaded
                    # The degraded list stays in place (and is retried) until the complete one is loaded
                    @current_stations_retry_at = nil
                    return @current_stations = loaded
                end
            end

            # Even the rebuilt file couldn't be read back: serve what was fetched, uncached, and retry later
            @current_stations = stations || @current_stations || []
            @current_stations_retry_at = Time.current.utc + CURRENT_STATIONS_RETRY
            logger.warn "serving current station list (#{@current_stations.length} stations) uncached, rebuilding after #{@current_stations_retry_at}"
            @current_stations
        end
    end

    # Incomplete in-memory list (some provider failed) that is due for another build
    def current_stations_retry_due?
        @current_stations_retry_at && Time.current.utc >= @current_stations_retry_at
    end

    def remove_current_station(station_id)
        # Under the lock, so a rebuild can't swap the list between the removal and the write
        @@current_stations_mutex.synchronize do
            @current_stations.delete_if { |s| s.id == station_id }
            # The list changed in place, so a table of better stations built from it is not used again
            (@demotion_list_versions ||= Hash.new(0))[:current] += 1
            # An incomplete list is never cached; it's rebuilt (with this station) on the next retry
            cache_current_stations(stations:@current_stations) unless @current_stations_retry_at
        end
    end

    def current_station_for(id)
        return nil if id.blank?
        station = current_stations.select { |s| s.id == id || s.bid == id }.first
        return station if station

        # Fallback to XTide engine
        xtide = current_clients(:xtide)
        if xtide.respond_to?(:engine)
            if data = xtide.engine.station_data(id, 'current').presence
                return Models::Station.from_hash({
                    'name' => data['name'],
                    'id' => id,
                    'bid' => id,
                    'public_id' => id,
                    'region' => data['region'],
                    'location' => data['name'],
                    'provider' => 'xtide',
                    'type' => data['type']
                })
            end
        end
        nil
    end

    # nil == any, units == [ mi, km ]
    def find_current_stations(by:nil, within:nil, units:'mi')
        by ||= [""]
        by &&= Array(by).map(&:downcase)

        logger.debug "finding current stations by #{by} within #{within}#{units}"

        by_stations = current_stations.select do |s|
            by.all? do |b|
                (s.bid.downcase.start_with?(b) rescue false) ||
                (s.id.downcase.start_with?(b) rescue false) ||
                (s.id.downcase.include?(b) rescue false) ||
                (s.name.downcase.include?(b) rescue false) ||
                (s.region.downcase.include?(b) rescue false)
            end
        end

        # Deduplicate multi-depth stations: keep only shallowest depth per base station ID
        # This matches production behavior where search results show one depth per station
        # (depth selection happens in UI after clicking on a station)
        by_stations = dedupe_current_stations_by_depth(by_stations)

        # can only do radius search with one result, ignore otherwise
        return by_stations unless within and by_stations.size == 1

        station = by_stations.first

        return find_current_stations_by_gps(station.lat, station.lon, within:within, units:units)
    end

    def find_current_stations_by_gps(lat, long, within:nil, units:'mi')
        within = within.to_i

        stations = current_stations.select do |s|
            Geocoder::Calculations.distance_between([lat, long], [s.lat,s.lon], units: units.to_sym) <= within
        end

        # Deduplicate multi-depth stations (same as find_current_stations)
        dedupe_current_stations_by_depth(stations)
    end

    # For current stations with multiple depth bins (same base ID, different BIDs),
    # keep only the shallowest depth. This matches production behavior where search
    # results show one representative depth per station (depth selection happens
    # in UI after clicking on a station).
    def dedupe_current_stations_by_depth(stations)
        by_base_id = stations.group_by { |s| s.id }

        by_base_id.map do |base_id, station_group|
            # If only one depth, return it
            next station_group.first if station_group.size == 1

            # Multiple depths: select shallowest (smallest depth value)
            # Stations without depth info go first (depth == nil treated as 0)
            station_group.min_by { |s| s.depth.to_f }
        end.compact
    end

    def cache_current_data_for(station, at:, around:)
        return false unless station

        current_data = current_clients(station.provider).current_data_for(station, around)

        # Nothing to cache for an empty list either -- it would serve "no currents" for the month
        if current_data.blank?
            logger.warn "no current data for #{station.bid} (#{station.provider}) around #{around.utc.to_date}, not caching #{at}"
            return false
        end

        logger.debug "storing current data at #{at}"
        atomic_write(at, current_data.map(&:to_h).to_json)

        return true
    end

    def current_data_for(station, around: Time.current.utc)
        return nil unless station

        datestamp = around.utc.strftime("%Y%m") # 202312
        filename  = "#{settings.cache_dir}/currents_v#{Models::CurrentData.version}_#{station.bid}_#{datestamp}#{harmonics_cache_key(station)}.json"
        return nil unless File.exist?(filename) || cache_current_data_for(station, at:filename, around:around)

        logger.debug "reading #{filename}"
        json = File.read(filename)

        logger.debug "parsing currents for #{station.bid}"
        data = JSON.parse(json) rescue []

        return data.map { |jc| Models::CurrentData.from_hash(jc) }
    end

    # Returns the next slack, flood, and ebb events for a station
    # Returns array of hashes: [{ type: 'Slack'|'Flood'|'Ebb', time: DateTime, velocity: Float? }, ...]
    def next_current_events(id, around: Time.current.utc)
        station = current_station_for(id) or return nil
        data = current_data_for(station, around: around) or return nil

        now = Time.current.utc
        future_data = data.select { |d| d.time > now }.sort_by(&:time)

        next_slack = future_data.find { |d| d.type == 'slack' }
        next_flood = future_data.find { |d| d.type == 'flood' }
        next_ebb = future_data.find { |d| d.type == 'ebb' }

        tz = timezone_for(station.lat, station.lon, station)

        events = []
        if next_slack
            events << {
                type: 'Slack',
                time: next_slack.time.in_time_zone(tz)
            }
        end
        if next_flood
            events << {
                type: 'Flood',
                time: next_flood.time.in_time_zone(tz),
                velocity: next_flood.velocity_major
            }
        end
        if next_ebb
            events << {
                type: 'Ebb',
                time: next_ebb.time.in_time_zone(tz),
                velocity: next_ebb.velocity_major
            }
        end

        # Sort by time so first max current is the soonest
        events.sort_by { |e| e[:time] }
    end

    def current_calendar_for(id, around: Time.current.utc)
        station = current_station_for(id) or return nil
        data    = current_data_for(station, around: around)

        return nil unless data

        cal = Icalendar::Calendar.new
        cal.x_wr_calname = station.name.titleize

        if station.provider.in?(['xtide', 'ticon'])
            cal.description = "NOT FOR NAVIGATION. This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  The author and the publisher each assume no liability for damages arising from use of these predictions.  They are not certified to be correct, and they do not incorporate the effects of tropical storms, El Niño, seismic events, subsidence, uplift, or changes in global sea level."
        end

        location = "#{station.name} (#{station.bid})"

        logger.debug "generating current calendar for #{location}"

        data.each do |current|
            date  = current.time.strftime("%Y-%m-%d")
            depth = current.depth ? " #{current.depth}ft" : ""
            title = case current.type
                    when "ebb"   then "Ebb #{current.velocity_major.to_f.abs}kts #{current.mean_ebb_dir}T#{depth}"
                    when "flood" then "Flood #{current.velocity_major}kts #{current.mean_flood_dir}T#{depth}"
                    when "slack" then "Slack"
                    end

            cal.event do |e|
                e.summary  = title
                e.dtstart  = Icalendar::Values::DateTime.new(current.time, tzid: 'GMT')
                e.dtend    = Icalendar::Values::DateTime.new(e.dtstart, tzid: 'GMT')
                e.url      = station.url + "?id=" + station.bid + "&d=" + date
                e.location = location if location
            end
        end

        cal.define_singleton_method(:station)  { station  }
        cal.define_singleton_method(:location) { location }

        logger.info "current calendar for #{location} generated with #{cal.events.length} events"

        return cal
    end

    ##
    ## Solar
    ##

    def solar_calendar_for(calendar, around:Time.current.utc)
        cal = Icalendar::Calendar.new
        cal.x_wr_calname = "Solar Events"

        from = beginning_of_window(around).strftime("%Y%m%d")
        to   = end_of_window(around).strftime("%Y%m%d")

        station  = calendar.station
        location = calendar.location

        logger.debug "generating solar calendar for #{from}-#{to}"

        (Date.parse(from)..Date.parse(to)).each do |date|
            tz      = timezone_for(station.lat, station.lon, station)
            calc    = SolarEventCalculator.new(date, station.lat, station.lon)
            # No sunrise or sunset during polar night or midnight sun.  RubySunrise returns nil
            # for the UTC time then, but its timezone conversion raises on nil, so check first.
            sunrise = calc.compute_official_sunrise(tz) if calc.compute_utc_official_sunrise
            sunset  = calc.compute_official_sunset(tz)  if calc.compute_utc_official_sunset

            # I dunno why tzid: GMT is correct vs. tzid: tz, but it works..
            cal.event do |e|
                e.summary  = "Sunrise"
                e.dtstart  = Icalendar::Values::DateTime.new(sunrise, tzid: 'GMT')
                e.dtend    = Icalendar::Values::DateTime.new(e.dtstart, tzid: 'GMT')
                e.location = location if location
            end if sunrise

            cal.event do |e|
                e.summary  = "Sunset"
                e.dtstart  = Icalendar::Values::DateTime.new(sunset, tzid: 'GMT')
                e.dtend    = Icalendar::Values::DateTime.new(e.dtstart, tzid: 'GMT')
                e.location = location if location
            end if sunset
        end

        logger.info "solar calendar for #{from}-#{to} generated with #{cal.events.length} events"

        cal.events.each do |e|
            calendar.add_event(e)
        end

        return cal
    end

    ##
    ## Lunar
    ##

    def lunar_phase_cache_file(year)
        "#{settings.cache_dir}/lunar_phases_#{year}.json"
    end

    def cache_lunar_phases(on:, phases:[])
        cache_file = lunar_phase_cache_file(on)

        logger.debug "storing #{phases.length} lunar phases for #{on} at #{cache_file}"
        atomic_write(cache_file, phases.to_json)

        return phases.length > 0
    end

    def lunar_phases(from, to)
        ret = []

        (from.year .. to.year).each do |year|
            # Thread-safe lazy initialization per year
            phases = @@lunar_phases_mutex.synchronize do
                @@lunar_phases ||= {}

                unless @@lunar_phases.key?(year)
                    cache_file = lunar_phase_cache_file(year)
                    unless File.exist?(cache_file)
                        unless year_phases = lunar_client.phases_for_year(year)
                            logger.error "failed to retrieve lunar phase data for #{year}"
                            next
                        end

                        cache_lunar_phases(on:year, phases:year_phases)
                    end

                    logger.debug "reading #{cache_file}"
                    json = File.read(cache_file)

                    logger.debug "parsing lunar phases for #{year}"
                    data = JSON.parse(json) rescue []

                    @@lunar_phases[year] = data.map do |phase|
                        {
                            datetime: DateTime.parse(phase["datetime"].to_s),
                            type:     phase["type"].to_sym,
                        }
                    end.sort_by { |phase| phase[:datetime] }
                end

                @@lunar_phases[year]
            end

            next unless phases
            ret << phases
        end

        return ret.flatten.select { |e| e[:datetime] >= from and e[:datetime] <= to }
    end

    def lunar_calendar_for(calendar, around:Time.current.utc)
        cal = Icalendar::Calendar.new
        cal.x_wr_calname = "Lunar Phases"

        from = beginning_of_window(around).strftime("%Y%m%d")
        to   = end_of_window(around).strftime("%Y%m%d")

        location = calendar.location

        logger.debug "generating lunar calendar for #{from}-#{to}"

        phase_names = {
            new_moon:      "New Moon",
            first_quarter: "First Quarter Moon",
            full_moon:     "Full Moon",
            last_quarter:  "Last Quarter Moon"
        }

        (lunar_phases(Date.parse(from), Date.parse(to)) || []).each do |phase|
            percent_full = case phase[:type]
                when :new_moon then 0
                when :first_quarter then 50
                when :last_quarter then 50
                when :full_moon then 100
                else (lunar_client.percent_full(phase[:datetime]) * 100).round # approximate
            end

            phase_time = phase[:datetime]

            cal.event do |e|
                e.summary     = phase_names[phase[:type]]
                e.description = "Moon is #{percent_full}% illuminated"
                e.dtstart     = Icalendar::Values::DateTime.new(phase_time, tzid: 'GMT')
                e.dtend       = Icalendar::Values::DateTime.new(phase_time + 1.second, tzid: 'GMT')
                e.location    = location if location
            end
        end

        logger.info "lunar calendar for #{from}-#{to} generated with #{cal.events.length} events"

        cal.events.each do |e|
            calendar.add_event(e)
        end

        return cal
    end

    ##
    ## Cache Management
    ##

    # Atomic file write: write to temp file then rename (prevents partial reads)
    def atomic_write(filename, content)
        temp_file = "#{filename}.tmp.#{$$}.#{Thread.current.object_id}"
        File.binwrite(temp_file, content)
        File.rename(temp_file, filename)
    rescue => e
        File.unlink(temp_file) rescue nil
        raise
    end

    # Lazily trigger cache cleanup on month rollover.  Called from request path;
    # uses try_lock so only one thread runs cleanup while others continue unblocked.
    # Cleanup runs in a background thread to avoid blocking the request.
    # Cross-process safe: uses a stamp file with flock to ensure only one worker runs cleanup.
    def cleanup_if_month_changed
        current_stamp = Time.current.utc.strftime("%Y%m")
        return if @@last_cleanup_stamp == current_stamp

        if @@cleanup_mutex.try_lock
            begin
                return if @@last_cleanup_stamp == current_stamp
                @@last_cleanup_stamp = current_stamp
                Thread.new { cleanup_old_cache_files }
            ensure
                @@cleanup_mutex.unlock
            end
        end
    end

    # Bulk cleanup of all old cache files (called on startup and on month rollover).
    # Uses a stamp file with flock for cross-process coordination in multi-worker Puma.
    def cleanup_old_cache_files
        current_stamp = Time.current.utc.strftime("%Y%m")
        current_quarter = "#{Time.current.utc.year}Q#{Time.current.utc.quarter}"

        # Set in-process stamp early so other threads in this worker don't re-trigger
        @@last_cleanup_stamp = current_stamp

        # Cross-process gate: only one worker runs cleanup per month
        stamp_file = "#{settings.cache_dir}/.cleanup_stamp"
        File.open(stamp_file, File::RDWR | File::CREAT) do |f|
            # Non-blocking exclusive lock; skip if another process holds it
            unless f.flock(File::LOCK_EX | File::LOCK_NB)
                logger.debug "cache cleanup: another process is running cleanup, skipping"
                return
            end

            # Check if cleanup already ran this month (by another worker)
            existing_stamp = f.read.strip
            if existing_stamp == current_stamp
                logger.debug "cache cleanup: already completed for #{current_stamp}"
                return
            end

            deleted_count = 0
            freed_bytes = 0

            Dir.glob("#{settings.cache_dir}/*").each do |file|
                next if File.directory?(file)
                basename = File.basename(file)

                should_delete = case basename
                when /_(20\d{4})[_.]/
                    $1 < current_stamp
                when /_(20\d{2}Q\d)[_.]/
                    $1 < current_quarter
                when /lunar_phases_(\d{4})\.json/
                    $1.to_i < Time.current.utc.year - 1
                else
                    false
                end

                if should_delete
                    freed_bytes += File.size(file) rescue 0
                    File.unlink(file) rescue nil
                    deleted_count += 1
                end
            end

            # Write stamp so other workers know cleanup is done
            f.rewind
            f.write(current_stamp)
            f.truncate(f.pos)
            f.flush

            logger.info "cache cleanup: removed #{deleted_count} files, freed #{freed_bytes / 1024 / 1024}MB"
        end
    end

end

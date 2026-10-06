require 'erb'
require_relative 'base'
require_relative '../models/station'
require_relative '../models/tide_data'

module Clients
    # Toitū Te Whenua Land Information New Zealand (LINZ) -- tide predictions for the New Zealand
    # standard ports, from the yearly CSV files LINZ publishes (no key, static files on S3).
    #
    # How often we fetch:
    # - station list: never; the ports, their LINZ numbers and positions are the table below
    #   (LINZ publishes positions only inside the CSV files, and adds a port rarely)
    # - a port's year file: once per station per requested month and year (WebCalTides caches each
    #   successful result to disk), and at most once per YEAR_CACHE_TTL per process for all months
    #   (in memory; the files rarely change after publication).  After an error, not again for
    #   UNAVAILABLE_RETRY (6h, in memory, per process): for the whole station after an HTTP error
    #   status or a network failure, for that year only after an unusable file or no body, and for
    #   that window only after an empty window.  A year that isn't published yet (S3 403/404) is
    #   left out for as long.
    #
    # Terms (linz.govt.nz/copyright): Crown copyright, licensed under CC BY 4.0.  LINZ asks for this
    # attribution on adapted work, in writing and without its logo: "This work is based on/includes
    # Toitū Te Whenua Land Information New Zealand data which are licensed by Toitū Te Whenua Land
    # Information New Zealand for re-use under the Creative Commons Attribution 4.0 International
    # licence."  CC BY also asks for a licence link and a note of what we changed (CHANGES).  LINZ
    # says these website predictions "are not official tide tables as specified in Maritime Rules
    # Part 25", so we never call them official.
    #
    # Times: the CSV rows are New Zealand local wall-clock time, "Local Std or Daylight Time", with
    # no offset; for the Chatham Islands ports, Chatham Islands time (their PDFs say "CHATHAM
    # ISLANDS LOCAL TIMES").  We convert them to UTC with the zone's DST rules.  In the hour that
    # repeats when daylight time ends (April), LINZ prints events from both sides of the change with
    # the same wall-clock time; see #resolve_ambiguous.
    #
    # Heights: metres above the standard port's chart datum.  The files don't say which events are
    # high and which are low; see #assign_types.
    class LinzTides < Base

        CSV_URL     = 'https://static.charts.linz.govt.nz/tide-tables/maj-ports/csv/%s'
        HOME_URL    = 'https://www.linz.govt.nz/products-services/tides-and-tidal-streams/tide-predictions'
        LICENSE_URL = 'https://creativecommons.org/licenses/by/4.0/'

        # LINZ's attribution sentence for adapted work (linz.govt.nz/copyright), with the links
        ATTRIBUTION = "This work is based on Toitū Te Whenua Land Information New Zealand data which are licensed by Toitū Te Whenua Land Information New Zealand for re-use under the Creative Commons Attribution 4.0 International licence (#{LICENSE_URL}). Source: LINZ tide predictions, #{HOME_URL}"

        # CC BY: indicate changes
        CHANGES = "Changes: times converted from New Zealand local time (Chatham Islands time for the Chatham Islands ports) to UTC; high and low water labels added from the order of the heights (the LINZ files do not label them); heights shown in metres or converted to feet; and the predictions presented as calendar events."

        DISCLAIMER = "NOT FOR NAVIGATION. These are LINZ website tide predictions, not the official tide tables specified in Maritime Rules Part 25; LINZ accepts no liability for their use."

        include TimeWindow

        def initialize(logger)
            super
            @unavailable       = {}
            @unavailable_mutex = Mutex.new
            @years             = {}
            @years_mutex       = Mutex.new
        end

        # Get a full year (1 month behind + now + 11 ahead).  LINZ publishes several years ahead.
        self.window_size = 13.months

        # Static files that change rarely, so a broken or missing one is unlikely to be fixed within
        # minutes; waiting longer would hide a transient failure for too long.
        UNAVAILABLE_RETRY = 6.hours

        # How long a downloaded year file is reused for other months, per process
        YEAR_CACHE_TTL = 1.day

        NZ_ZONE      = 'Pacific/Auckland'
        CHATHAM_ZONE = 'Pacific/Chatham'

        # Transport failures get_url can raise (after its retries, for timeouts): no usable response,
        # as opposed to an HTTP error status (Mechanize::ResponseCodeError, rescued before these).
        # Mechanize::Error covers a truncated or cut-short body (ResponseReadError,
        # ChunkedTerminationError), an undecodable content-encoding and too many redirects.
        NETWORK_ERRORS = [
            Timeout::Error, SocketError, SystemCallError, OpenSSL::SSL::SSLError, EOFError, IOError,
            Net::HTTPBadResponse, Net::HTTP::Persistent::Error, Mechanize::Error
        ].freeze

        # S3 answers 403 (not 404) for a file that doesn't exist, e.g. a year LINZ hasn't published yet
        MISSING_CODES = %w[403 404].freeze

        # "070,Auckland,36°51'S,174°46'E" (some positions have decimal minutes, one lacks the ')
        HEADER = /\A\s*(\d+),(.+?),\s*\d+°[\d.]+'?[NS],\s*\d+°[\d.]+'?[EW]\s*\z/
        TIMES  = /\ALocal Std or Daylight Time\b/
        HEIGHTS = /Tidal heights in metres/
        CLOCK  = /\A(\d{2}):(\d{2})\z/

        Port = Struct.new(:name, :slug, :number, :lat, :lon, :linz_name, keyword_init: true) do
            def zone = name.include?('Chatham Island') ? CHATHAM_ZONE : NZ_ZONE
            def antarctic? = name == 'Scott Base'
        end

        # The LINZ standard ports with daily predictions (tide-predictions-list-view, 6 Oct 2026):
        # file name, our id slug (never change one: subscribers' feed URLs contain it), LINZ port
        # number and position from the CSV header line, and the header's own spelling of the name
        # where it differs from the file name.
        PORTS = [
            ["Akaroa",                      "akaroa",                     "356",  -43.8000,  172.9667],
            ["Anakakata Bay",               "anakakata-bay",              "421",  -41.0500,  174.2833],
            ["Anawhata",                    "anawhata",                   "372",  -36.9333,  174.4500],
            ["Auckland",                    "auckland",                   "070",  -36.8500,  174.7667],
            ["Ben Gunn Wharf",              "ben-gunn-wharf",             "376",  -35.0000,  173.2667],
            ["Bluff",                       "bluff",                      "072",  -46.6000,  168.3500],
            ["Castlepoint",                 "castlepoint",                "313",  -40.9167,  176.2167],
            ["Charleston",                  "charleston",                 "407",  -41.9083,  171.4333],
            ["Dargaville",                  "dargaville",                 "210",  -35.9333,  173.8667],
            ["Deep Cove",                   "deep-cove",                  "019",  -45.4667,  167.1500],
            ["Dog Island",                  "dog-island",                 "353",  -46.6500,  168.4167],
            ["Dunedin",                     "dunedin",                    "028",  -45.8833,  170.5000],
            ["Elaine Bay",                  "elaine-bay",                 "105",  -41.0500,  173.7667],
            ["Elie Bay",                    "elie-bay",                   "446",  -41.1317,  173.9917],
            ["Fishing Rock - Raoul Island", "fishing-rock-raoul-island",  "198",  -29.2500, -177.9167],
            ["Flour Cask Bay",              "flour-cask-bay",             "015",  -47.2833,  167.4833],
            ["Fresh Water Basin",           "fresh-water-basin",          "330",  -44.6667,  167.9333],
            ["Gisborne",                    "gisborne",                   "078",  -38.6667,  178.0333],
            ["Green Island",                "green-island",               "354",  -45.9500,  170.3833],
            ["Halfmoon Bay - Oban",         "halfmoon-bay-oban",          "056",  -46.9000,  168.1333, "Halfmoon Bay / Oban"],
            ["Havelock",                    "havelock",                   "017",  -41.2833,  173.7667],
            ["Helensville",                 "helensville",                "239",  -36.6667,  174.4500],
            ["Huruhi Harbour",              "huruhi-harbour",             "140",  -36.6000,  175.7667],
            ["Jackson Bay",                 "jackson-bay",                "142",  -43.9833,  168.6333],
            ["Kaikōura",                    "kaikoura",                   "106",  -42.4167,  173.7000],
            ["Kaingaroa - Chatham Island",  "kaingaroa-chatham-island",   "374",  -43.7333, -176.2667],
            ["Kaiteriteri",                 "kaiteriteri",                "349",  -41.0500,  173.0167],
            ["Kaituna River Entrance",      "kaituna-river-entrance",     "397",  -37.7500,  176.4167, "Kaituna River"],
            ["Kawhia",                      "kawhia",                     "044",  -38.0667,  174.8167],
            ["Korotiti Bay",                "korotiti-bay",               "371",  -36.1833,  175.4833],
            ["Leigh",                       "leigh",                      "054",  -36.2833,  174.8000],
            ["Long Island",                 "long-island",                "207",  -41.1167,  174.2833],
            ["Lottin Point - Wakatiri",     "lottin-point-wakatiri",      "357",  -37.5500,  178.1667, "Lottin Point / Wakatiri"],
            ["Lyttelton",                   "lyttelton",                  "107",  -43.6000,  172.7167],
            ["Man o‘War Bay",               "man-owar-bay",               "147",  -36.7833,  175.1500, "Man O' War Bay"],
            ["Mana Marina",                 "mana-marina",                "323",  -41.1000,  174.8667],
            ["Manu Bay",                    "manu-bay",                   "396",  -37.8167,  174.8167],
            ["Marsden Point",               "marsden-point",              "079",  -35.8333,  174.5000],
            ["Motuara Island",              "motuara-island",             "472",  -41.0933,  174.2717],
            ["Moturiki Island",             "moturiki-island",            "158",  -37.6333,  176.1833],
            ["Māpua",                       "mapua",                      "206",  -41.2500,  173.1000],
            ["Mātiatia Bay",                "matiatia-bay",               "150",  -36.7833,  174.9833],
            ["Napier",                      "napier",                     "097",  -39.4833,  176.9167],
            ["Nelson",                      "nelson",                     "077",  -41.2667,  173.2667],
            ["New Brighton Pier",           "new-brighton-pier",          "468",  -43.5067,  172.7350],
            ["North Cape - Otou",           "north-cape-otou",            "162",  -34.4167,  173.0333, "North Cape / Otou"],
            ["Oamaru",                      "oamaru",                     "164",  -45.1000,  170.9833],
            ["Omaha Bridge",                "omaha-bridge",               "462",  -36.3417,  174.7650],
            ["Onehunga",                    "onehunga",                   "027",  -36.9333,  174.7833],
            ["Opononi",                     "opononi",                    "171",  -35.5000,  173.4000],
            ["Opua",                        "opua",                       "085",  -35.3167,  174.1167],
            ["Owenga - Chatham Island",     "owenga-chatham-island",      "352",  -44.0333, -176.3667],
            ["Paratutae Island",            "paratutae-island",           "090",  -37.0500,  174.5167],
            ["Picton",                      "picton",                     "043",  -41.2833,  174.0000],
            ["Port Chalmers",               "port-chalmers",              "030",  -45.8167,  170.6500],
            ["Port Taranaki",               "port-taranaki",              "076",  -39.0500,  174.0333],
            ["Port Ōhope Wharf",            "port-ohope-wharf",           "102",  -37.9833,  177.1000],
            ["Pouto Point",                 "pouto-point",                "081",  -36.3667,  174.1833],
            ["Raglan",                      "raglan",                     "180",  -37.8000,  174.8833],
            ["Rangatira Point",             "rangatira-point",            "351",  -40.8500,  174.9333],
            ["Rangitaiki River Entrance",   "rangitaiki-river-entrance",  "398",  -37.9167,  176.8667, "Rangitaiki River"],
            ["Richmond Bay",                "richmond-bay",               "445",  -41.0150,  173.9883],
            ["Riverton - Aparima",          "riverton-aparima",           "058",  -46.3667,  168.0167],
            ["Scott Base",                  "scott-base",                 "333",  -77.8333,  166.6667],
            ["Spit Wharf",                  "spit-wharf",                 "032",  -45.7833,  170.7167],
            ["Sumner Head",                 "sumner-head",                "370",  -43.5667,  172.7667],
            ["Tarakohe",                    "tarakohe",                   "189",  -40.8167,  172.9000],
            ["Tauranga",                    "tauranga",                   "073",  -37.6500,  176.1833],
            ["Thames",                      "thames",                     "233",  -37.1333,  175.5167],
            ["Timaru",                      "timaru",                     "034",  -44.3833,  171.2500],
            ["Town Basin",                  "town-basin",                 "373",  -35.7167,  174.3333, "Town Basin - Whangarei"],
            ["Tāmaki River",                "tamaki-river",               "463",  -36.9117,  174.8617],
            ["Waihopai River Entrance",     "waihopai-river-entrance",    "141",  -46.4167,  168.3333],
            ["Waitangi - Chatham Island",   "waitangi-chatham-island",    "094",  -43.9500, -176.5667],
            ["Weiti River Entrance",        "weiti-river-entrance",       "291",  -36.6500,  174.7333],
            ["Welcombe Bay",                "welcombe-bay",               "355",  -46.0833,  166.5833],
            ["Wellington",                  "wellington",                 "071",  -41.2833,  174.7833],
            ["Westport",                    "westport",                   "074",  -41.7500,  171.6000],
            ["Whakatāne",                   "whakatane",                  "031",  -37.9500,  177.0000],
            ["Whanganui River Entrance",    "whanganui-river-entrance",   "075",  -39.9500,  174.9833],
            ["Whangaroa",                   "whangaroa",                  "401",  -35.0500,  173.7500],
            ["Whangārei",                   "whangarei",                  "108",  -35.7667,  174.3500],
            ["Whitianga",                   "whitianga",                  "022",  -36.8333,  175.7000],
            ["Wilson Bay",                  "wilson-bay",                 "237",  -41.0833,  173.9000],
            ["Ōkukari Bay",                 "okukari-bay",                "258",  -41.2000,  174.3167],
            ["Ōmokoroa",                    "omokoroa",                   "261",  -37.6667,  176.0500],
            ["Ōpōtiki Wharf",               "opotiki-wharf",              "104",  -38.0333,  177.2333],
        ].map { |name, slug, number, lat, lon, linz_name| Port.new(name: name, slug: slug, number: number, lat: lat, lon: lon, linz_name: linz_name) }.freeze

        # Feed-level credit + what we changed + disclaimer
        def self.feed_description(_tides = nil)
            "#{ATTRIBUTION}. #{CHANGES} #{DISCLAIMER}"
        end

        # Per-event credit
        def self.event_description(_tide = nil)
            "Based on Toitū Te Whenua Land Information New Zealand (LINZ) data, CC BY 4.0 (#{LICENSE_URL}). NOT FOR NAVIGATION."
        end

        # Station id: 'NZ__' + the port's slug, e.g. Port Chalmers -> NZ__port-chalmers
        def self.station_id_for(slug)
            "NZ__#{slug}"
        end

        MACRON_FOLD = { 'ā' => 'a', 'ē' => 'e', 'ī' => 'i', 'ō' => 'o', 'ū' => 'u', 'Ā' => 'A', 'Ē' => 'E', 'Ī' => 'I', 'Ō' => 'O', 'Ū' => 'U' }.freeze

        # Other spellings to search by: plain-ASCII (no macrons) forms, so "Whakatane" and
        # "Kaikoura" find Whakatāne and Kaikōura; LINZ's own header spelling where it differs
        # (e.g. "Halfmoon Bay / Oban", "Town Basin - Whangarei"); and "<name>, NZL", the form the
        # TICON stations use, so "Auckland NZ" and "Auckland NZL" find the LINZ port too.
        def self.alternate_names(port)
            names  = [port.name, port.linz_name].compact
            folded = names.map { |n| n.gsub(/[āēīōūĀĒĪŌŪ]/, MACRON_FOLD).tr('‘’', "''") }
            (names + folded + ["#{folded.first}, NZL"]).uniq - [port.name]
        end

        def self.port_for(station_id)
            @ports_by_id ||= PORTS.to_h { |port| [station_id_for(port.slug), port] }.freeze
            @ports_by_id[station_id]
        end

        def tide_stations
            # nil (not []) if the table were ever empty, so the caller doesn't cache "no NZ ports"
            if PORTS.empty?
                logger.error "!! LINZ port table is empty"
                return nil
            end

            PORTS.map do |port|
                Models::Station.new(
                    name: port.name,
                    alternate_names: self.class.alternate_names(port),
                    id: self.class.station_id_for(port.slug),
                    public_id: port.number,
                    region: 'New Zealand',
                    location: [port.name, port.antarctic? ? 'Antarctica' : 'New Zealand'].join(", "),
                    lat: port.lat,
                    lon: port.lon,
                    url: HOME_URL,
                    provider: 'linz'
                )
            end
        end

        def tide_data_for(station, around)
            from = beginning_of_window(around)
            to   = end_of_window(around)

            # A station-level failure (HTTP error status, network failure, unknown port) blocks every
            # window; an unusable year file the windows that need that year (#year_rows); an empty
            # window only this one
            window_key = "#{station.id}@#{around.utc.strftime('%Y%m')}"
            if (until_time = unavailable_until(station.id) || unavailable_until(window_key))
                logger.debug "skipping LINZ tide data for #{station.id}: unavailable, next try after #{until_time}"
                return nil
            end

            unless (port = self.class.port_for(station.id))
                logger.error "!! no LINZ port for station #{station.id}"
                return unavailable!(station.id)
            end

            zone  = TZInfo::Timezone.get(port.zone)
            years = zone.utc_to_local(from.utc).year..zone.utc_to_local(to.utc).year

            rows    = []
            heights = true
            years.each do |year|
                # An unusable year file fails only the windows that need it
                parsed = year_rows(station, port, year) or return nil
                next if parsed == :missing
                heights &&= parsed[:heights]
                rows.concat(parsed[:rows])
            end

            events = to_utc(station, rows, zone)
            assign_types(events)

            tides = events.filter_map do |ev|
                next unless ev[:type] && ev[:time] >= from && ev[:time] <= to

                Models::TideData.new(
                    type: ev[:type],
                    units: "m",
                    prediction: heights ? ev[:height] : nil,
                    time: ev[:time].to_datetime.new_offset(0),
                    url: station.url
                )
            end

            if tides.empty?
                logger.error "!! no LINZ tide data for station #{station.id} between #{from.strftime('%Y-%m-%d')} and #{to.strftime('%Y-%m-%d')} (years: #{years.to_a.join(', ')})"
                return unavailable!(window_key)
            end

            return tides
        end

        private

        # { rows: [[local wall-clock Time (as UTC fields), height], ...], heights: bool } for a port's
        # year file; :missing if LINZ hasn't published it; nil (that year marked unavailable) if the
        # file came without a body or couldn't be used.  An HTTP error status or a network failure
        # raises (from #year_file, station marked unavailable).
        def year_rows(station, port, year)
            year_key = "#{station.id}/#{year}"
            return :missing if unavailable_until(year_key)
            if (until_time = unavailable_until(unusable_key(station, year)))
                logger.debug "skipping LINZ tide data for #{station.id}: #{year} file unusable, next try after #{until_time}"
                return nil
            end

            body = year_file(station, port, year) or return nil
            if body == :missing
                unavailable!(year_key)
                return :missing
            end

            url   = csv_url(port, year)
            text  = decode(station, url, body)
            lines = text.delete_prefix("\uFEFF").split(/\r?\n/)
            header = lines[0].to_s.match(HEADER)

            # The file must be this port's (LINZ number), in local time
            unless header && header[1] == port.number && lines[2].to_s.match?(TIMES)
                logger.error "!! LINZ tide data for station #{station.id} at #{url} unusable (expected port #{port.number} in local time), body starts #{text[0, 100].inspect}, next try in #{UNAVAILABLE_RETRY.inspect}"
                forget_year(port, year)
                return unavailable!(unusable_key(station, year))
            end

            heights = lines[2].match?(HEIGHTS)
            unless heights
                logger.warn "omitting heights of LINZ data for station #{station.id}: unexpected units line #{lines[2][0, 100].inspect}"
            end

            skipped = []
            rows = lines[3..].reject(&:blank?).flat_map do |line|
                fields = line.split(',', -1).map(&:strip)
                day, month, yr = Integer(fields[0], exception: false), Integer(fields[2], exception: false), Integer(fields[3], exception: false)
                unless yr == year && month && day && Date.valid_date?(yr, month, day)
                    skipped << line
                    next []
                end

                fields[4..].each_slice(2).filter_map do |clock, height|
                    next if clock.blank? && height.blank?
                    hm = clock.to_s.match(CLOCK)
                    h  = Float(height, exception: false)
                    unless hm && hm[1].to_i < 24 && hm[2].to_i < 60 && h
                        skipped << line
                        next
                    end
                    [Time.utc(yr, month, day, hm[1].to_i, hm[2].to_i), h]
                end
            end

            if skipped.any?
                logger.warn "skipping #{skipped.length} unreadable LINZ rows/events of #{year} for station #{station.id}, e.g. #{skipped.first[0, 200].inspect}"
            end

            if rows.empty?
                logger.error "!! LINZ tide data for station #{station.id} at #{url} has no events, body starts #{text[0, 100].inspect}, next try in #{UNAVAILABLE_RETRY.inspect}"
                forget_year(port, year)
                return unavailable!(unusable_key(station, year))
            end

            { rows: rows.sort_by(&:first), heights: heights }
        end

        # The body of a port's year file (cached in memory for YEAR_CACHE_TTL), :missing for S3's
        # 403/404, or nil (that year marked unavailable) when the response has no body.  Any other
        # HTTP error status or a network failure is logged, marks the station unavailable (an
        # outage isn't specific to one year) and is re-raised.
        def year_file(station, port, year)
            key = "#{port.number}/#{year}"
            @years_mutex.synchronize do
                cached = @years[key]
                return cached[:body] if cached && Time.current.utc < cached[:until]
                @years.delete(key)
            end

            url = csv_url(port, year)
            logger.info "getting tide data from #{url}"

            begin
                unless body = get_url(url)
                    logger.error "!! got no LINZ tide data for station #{station.id} from #{url}, next try in #{UNAVAILABLE_RETRY.inspect}"
                    return unavailable!(unusable_key(station, year))
                end
            rescue Mechanize::ResponseCodeError => e
                if MISSING_CODES.include?(e.response_code)
                    logger.warn "#{e.response_code} for #{year} LINZ data of station #{station.id} (not published yet?), leaving the year out, next try in #{UNAVAILABLE_RETRY.inspect}"
                    return :missing
                end
                # Back off for any other HTTP error, so an outage doesn't turn every feed request into another call
                logger.error "!! LINZ tide data for station #{station.id} at #{url} failed (HTTP #{e.response_code}), next try in #{UNAVAILABLE_RETRY.inspect}"
                unavailable!(station.id)
                raise
            rescue *NETWORK_ERRORS => e
                # get_url has already retried timeouts; back off so an outage costs one call per
                # station per UNAVAILABLE_RETRY instead of a full retry sequence on every feed request
                logger.error "!! LINZ tide data for station #{station.id} unreachable (#{e.class}: #{e.message}), next try in #{UNAVAILABLE_RETRY.inspect}"
                unavailable!(station.id)
                raise
            end

            @years_mutex.synchronize { @years[key] = { body: body, until: Time.current.utc + YEAR_CACHE_TTL } }
            body
        end

        # The space and any macron are percent-encoded, e.g. .../csv/Whakat%C4%81ne%202027.csv
        def csv_url(port, year)
            CSV_URL % ERB::Util.url_encode("#{port.name} #{year}.csv")
        end

        # Negative-cache key for one station's unusable year file
        def unusable_key(station, year)
            "#{station.id}/#{year} unusable"
        end

        # The files are UTF-8, except some older ones in Windows-1252 (29 ports' 2025 files, whose
        # degree sign in the header is the single byte 0xB0).  ISO-8859-1 maps every byte, for the
        # few that Windows-1252 leaves undefined, so a file that is neither still reaches the
        # header check and is reported there.
        def decode(station, url, body)
            text = body.dup.force_encoding('UTF-8')
            return text if text.valid_encoding?

            logger.info "LINZ tide data for station #{station.id} at #{url} is not UTF-8, reading it as Windows-1252"
            begin
                text.force_encoding('Windows-1252').encode('UTF-8')
            rescue EncodingError
                body.dup.force_encoding('ISO-8859-1').encode('UTF-8')
            end
        end

        def forget_year(port, year)
            @years_mutex.synchronize { @years.delete("#{port.number}/#{year}") }
        end

        # [{ time: UTC Time, height: }, ...] in time order.  Wall-clock times are converted with the
        # zone's rules.  A time in the hour skipped when daylight time starts doesn't exist, so it is
        # skipped with a warning (the 2026-2027 files of all 87 ports have none).
        def to_utc(station, rows, zone)
            events = rows.filter_map do |local, height|
                offsets = zone.periods_for_local(local).map(&:observed_utc_offset).uniq.sort.reverse
                if offsets.empty?
                    logger.warn "skipping LINZ event for station #{station.id} at #{local.strftime('%Y-%m-%d %H:%M')}, a local time that doesn't exist in #{zone.identifier}"
                    next
                end
                { candidates: offsets.map { |o| local - o }, height: height }
            end

            events.each_with_index do |ev, i|
                ev[:time] = ev[:candidates].length == 1 ? ev[:candidates].first : resolve_ambiguous(station, events, i)
            end

            events.sort_by { |ev| ev[:time] }
        end

        # When daylight time ends, an hour of wall-clock times happens twice (02:00-02:59 NZ, 02:45-03:44
        # Chatham).  LINZ's PDFs print the times "adjusted for daylight time" in bold, and in that hour
        # both bold (before the change) and plain (after it) times occur, so an event there can be
        # either; the CSV doesn't say which.  Tides repeat with the lunar day, so we take the
        # candidate nearer the midpoint of the events four before and four after (about a day either
        # side).  Checked against the bold marks in the LINZ PDFs: right for all 58 such events of
        # 2026-2027 (87 ports; 26 daylight, 32 standard), with the wrong candidate at least 48 min
        # further from the midpoint than the right one.
        def resolve_ambiguous(station, events, i)
            ev = events[i]
            before, after = events[i - 4], events[i + 4] if i >= 4
            if before && after && before[:candidates].length == 1 && after[:candidates].length == 1
                mid = before[:candidates].first + (after[:candidates].first - before[:candidates].first) / 2
                return ev[:candidates].min_by { |t| (t - mid).abs }
            end

            # Not enough neighbours (can't happen inside a year file: the change is in April)
            logger.warn "LINZ event for station #{station.id} at #{ev[:candidates].first.utc} is in the repeated hour without neighbours to tell which; taking the daylight-time reading"
            ev[:candidates].first
        end

        # High or low: higher (lower) than both neighbours.  A height equal to a neighbour's (LINZ
        # rounds to 0.1 m, so small-range ports have some) is decided by alternation with the
        # nearest decided event.  Checked on all 87 ports' 2026-2027 files: 244,114 events decided by
        # their neighbours, 122 by alternation, none out of order (rising or falling three in a row).
        def assign_types(events)
            last = events.length - 1

            events.each_with_index do |ev, i|
                neighbours = [i - 1, i + 1].select { |k| k.between?(0, last) }.map { |k| events[k][:height] }
                next if neighbours.empty?

                ev[:type] = if neighbours.all? { |h| ev[:height] > h } then 'High'
                            elsif neighbours.all? { |h| ev[:height] < h } then 'Low'
                            end
            end

            events.each_with_index do |ev, i|
                next if ev[:type]
                j = (1..last).lazy.flat_map { |d| [i - d, i + d] }.find { |k| k.between?(0, last) && events[k][:type] } or next
                ev[:type] = (j - i).even? ? events[j][:type] : (events[j][:type] == 'High' ? 'Low' : 'High')
            end
        end

        # Negative cache for stations (or station + month, station / year) without usable data, so
        # we don't refetch on every request (WebCalTides only caches successful results).  Per
        # process, in memory.
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

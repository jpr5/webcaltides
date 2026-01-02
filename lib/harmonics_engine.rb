require 'json'
require 'fileutils'
require 'date'
require 'active_support/all'

module Harmonics
    class Engine
        HARMONICS_FILE = File.expand_path('../data/latest-harmonics.sql', __dir__)
        TICON_FILE = File.expand_path('../data/latest-ticon.json', __dir__)

        attr_reader :speeds, :stations_cache, :harmonics_file, :ticon_file, :logger

        # Astronomical constants from SP 98 Table 1 (via libcongen)
        # These are fixed for the 1900 epoch to match the harmonic data
        OBLIQUITY = 23.0 + 27.0/60.0 + 8.26/3600.0 # omega (degrees)
        LUNAR_INCLINATION = 5.0 + 8.0/60.0 + 43.3546/3600.0 # i (degrees)
        DAYS_PER_JULIAN_CENTURY = 36525.0
        SECONDS_PER_JULIAN_CENTURY = 3155760000.0
        TABLE_1_EPOCH = Time.find_zone('UTC').local(1899, 12, 31, 12, 0, 0)

        # Base constituents for compound calculations (from libcongen)
        # Order matching libcongen.cc: O1, K1, P1, M2, S2, N2, L2, K2, Q1, nu2, S1, M1-DUTCH, lambda2
        BASES_ORDER = ['O1', 'K1', 'P1', 'M2', 'S2', 'N2', 'L2', 'K2', 'Q1', 'NU2', 'S1', 'M1', 'LDA2']
        BASES = {
            'O1' => { 'type' => 'Basic', 'v' => [1, -2, 1, 0, 0, 90], 'u' => [2, -1, 0, 0, 0, 0, 0], 'f_formula' => 75 },
            'K1' => { 'type' => 'Basic', 'v' => [1, 0, 1, 0, 0, -90], 'u' => [0, 0, -1, 0, 0, 0, 0], 'f_formula' => 227 },
            'P1' => { 'type' => 'Basic', 'v' => [1, 0, -1, 0, 0, 90], 'u' => [0, 0, 0, 0, 0, 0, 0], 'f_formula' => 1 },
            'M2' => { 'type' => 'Basic', 'v' => [2, -2, 2, 0, 0, 0], 'u' => [2, -2, 0, 0, 0, 0, 0], 'f_formula' => 78 },
            'S2' => { 'type' => 'Basic', 'v' => [2, 0, 0, 0, 0, 0], 'u' => [0, 0, 0, 0, 0, 0, 0], 'f_formula' => 1 },
            'N2' => { 'type' => 'Basic', 'v' => [2, -3, 2, 1, 0, 0], 'u' => [2, -2, 0, 0, 0, 0, 0], 'f_formula' => 78 },
            'L2' => { 'type' => 'Basic', 'v' => [2, -1, 2, -1, 0, 180], 'u' => [2, -2, 0, 0, 0, -1, 0], 'f_formula' => 215 },
            'K2' => { 'type' => 'Basic', 'v' => [2, 0, 2, 0, 0, 0], 'u' => [0, 0, 0, -1, 0, 0, 0], 'f_formula' => 235 },
            'Q1' => { 'type' => 'Basic', 'v' => [1, -3, 1, 1, 0, 90], 'u' => [2, -1, 0, 0, 0, 0, 0], 'f_formula' => 75 },
            'NU2' => { 'type' => 'Basic', 'v' => [2, -3, 4, -1, 0, 0], 'u' => [2, -2, 0, 0, 0, 0, 0], 'f_formula' => 78 },
            'S1' => { 'type' => 'Basic', 'v' => [1, 0, 0, 0, 0, 0], 'u' => [0, 0, 0, 0, 0, 0, 0], 'f_formula' => 1 },
            'M1' => { 'type' => 'Basic', 'v' => [1, -1, 1, 1, 0, -90], 'u' => [0, -1, 0, 0, 0, 0, -1], 'f_formula' => 206 },
            'LDA2' => { 'type' => 'Basic', 'v' => [2, -1, 0, 1, 0, 180], 'u' => [2, -2, 0, 0, 0, 0, 0], 'f_formula' => 78 }
        }

        def initialize(logger, cache_dir = nil)
            @logger = logger
            @harmonics_file = ENV['HARMONICS_FILE'] || HARMONICS_FILE
            @ticon_file = ENV['TICON_FILE'] || TICON_FILE
            @cache_dir = cache_dir || 'cache'
            @stations_cache = {}
            @speeds = {}
            @constituent_definitions = {}
            @nodal_factors_cache = {}
            @parsed_stations = nil
        end

        def stations
            @parsed_stations ||= load_stations_from_cache || begin
                xtide_stations = parse_harmonics_file
                ticon_stations = parse_ticon_file
                merged = xtide_stations + ticon_stations
                save_stations_to_cache(merged)
                merged
            end
        end

        def generate_predictions(station_id, start_time, end_time)
            station_data = @stations_cache[station_id] || {}
            constituents = station_data['constituents'] || []

            if constituents.empty?
                @logger.warn "No constituents found for station #{station_id}"
                return []
            end

            datum_offset = station_data['datum_offset'] || 0.0
            meridian_offset = parse_meridian(station_data['meridian'])
            units = station_data['units'] || 'ft'

            # Use UTC for all astronomical calculations
            start_utc = start_time.utc
            end_utc = end_time.utc

            predictions = []
            current_utc = start_utc

            # Keep track of the current nodal factors to avoid re-calculating/re-loading mid-loop
            current_day_key = nil
            nodal = nil
            year_start_utc = nil

            while current_utc <= end_utc
                # Update nodal factors if we cross into a new day
                day_key = "#{current_utc.year}_#{current_utc.month}_#{current_utc.day}"
                if day_key != current_day_key
                    current_day_key = day_key
                    nodal = get_nodal_factors(current_utc.year, current_utc.month, current_utc.day, meridian_offset)
                    year_start_utc = Time.new(current_utc.year, 1, 1, 0, 0, 0, 0).utc
                end

                height = datum_offset
                # t is hours from start of year UTC
                t = (current_utc - year_start_utc) / 3600.0

                # Formula: V = V0 + speed * t + u - phase
                # If meridian is West-positive (e.g. 5 for EST), then t_lst = t_utc - 5.
                # Since V0 is calculated for Jan 1 00:00:00 LST, we must use t relative to LST.
                t -= meridian_offset

                constituents.each do |c|
                    name = c['name']
                    speed = @speeds[name] || @constituent_definitions[name]&.[]('speed')
                    next unless speed

                    nf = nodal[name] || { 'f' => 1.0, 'u' => 0.0, 'V0' => 0.0 }
                    # arg = (speed * t + (V0 + u) - phase)
                    arg = (speed * t + (nf['V0'] + nf['u']) - c['phase']) * Math::PI / 180.0
                    height += nf['f'] * c['amp'] * Math.cos(arg)
                end

                predictions << { 'time' => current_utc, 'height' => height, 'units' => units }
                current_utc += 1.minute
            end

            predictions
        end

        def detect_peaks(predictions)
            peaks = []
            (1...predictions.length-1).each do |i|
                prev = predictions[i-1]
                curr = predictions[i]
                nxt  = predictions[i+1]

                if (curr['height'] > prev['height'] && curr['height'] > nxt['height']) ||
                   (curr['height'] < prev['height'] && curr['height'] < nxt['height'])

                    type = curr['height'] > prev['height'] ? 'High' : 'Low'

                    # Refine peak using parabolic fitting for sub-minute precision
                    y1, y2, y3 = prev['height'], curr['height'], nxt['height']
                    denom = (y1 - 2*y2 + y3)

                    if denom != 0
                        offset_seconds = ((y1 - y3) / (2.0 * denom)) * 60.0
                        refined_time = curr['time'] + offset_seconds.seconds
                        refined_height = y2 - ((y1 - y3)**2 / (8.0 * denom))
                    else
                        refined_time = curr['time']
                        refined_height = y2
                    end

                    peaks << {
                        'type' => type,
                        'height' => refined_height.round(3),
                        'time' => refined_time,
                        'units' => curr['units']
                    }
                end
            end
            peaks
        end

        private

        def load_stations_from_cache
            cache_file = "#{@cache_dir}/harmonics_stations.json"

            # Check if cache is newer than BOTH source files
            sources = [@harmonics_file]
            sources << @ticon_file if File.exist?(@ticon_file)

            latest_source_time = sources.map { |f| File.mtime(f) }.max

            if File.exist?(cache_file) && File.mtime(cache_file) > latest_source_time
                @logger.debug "Loading merged stations from cache: #{cache_file}"
                data = JSON.parse(File.read(cache_file))

                stations = []
                @stations_cache = {}
                @speeds = data['speeds'] || {}

                data['stations'].each do |h|
                    metadata = h['metadata']
                    station_id = metadata['id']
                    stations << metadata
                    @stations_cache[station_id] = {
                        'constituents' => h['constituents'],
                        'datum_offset' => h['datum_offset'] || 0.0,
                        'timezone' => h['timezone'] || metadata['timezone'],
                        'meridian' => h['meridian'] || metadata['meridian'],
                        'units' => h['units'] || metadata['units']
                    }
                end
                return stations
            end
            nil
        end

        def save_stations_to_cache(stations)
            FileUtils.mkdir_p(@cache_dir)
            cache_file = "#{@cache_dir}/harmonics_stations.json"
            @logger.debug "Caching harmonics stations to: #{cache_file}"

            cache_data = {
                'speeds' => @speeds,
                'stations' => stations.map do |s|
                    cache_entry = @stations_cache[s['id']]
                    {
                        'metadata' => s,
                        'constituents' => cache_entry['constituents'],
                        'datum_offset' => cache_entry['datum_offset'],
                        'timezone' => cache_entry['timezone'],
                        'meridian' => cache_entry['meridian'],
                        'units' => cache_entry['units']
                    }
                end
            }
            File.write(cache_file, cache_data.to_json)
        end

        def parse_harmonics_file
            @logger.info "Parsing SQL harmonics file: #{@harmonics_file}"
            stations_by_index = {}
            constants_by_index = Hash.new { |h, k| h[k] = [] }
            current_table = nil

            # Use ISO-8859-1 (Latin-1) encoding as specified in the SQL file
            File.foreach(@harmonics_file, encoding: 'ISO-8859-1:UTF-8') do |line|
                line.strip!

                if line.start_with?('COPY public.constituents')
                    current_table = :constituents
                    next
                elsif line.start_with?('COPY public.data_sets')
                    current_table = :data_sets
                    next
                elsif line.start_with?('COPY public.constants')
                    current_table = :constants
                    next
                elsif line == '\.'
                    current_table = nil
                    next
                end

                next unless current_table
                parts = line.split("\t")

                case current_table
                when :constituents
                    # (name, definition, speed)
                    name = parts[0]
                    definition_str = parts[1]
                    speed = parts[2].to_f
                    @speeds[name] = speed

                    # Parse definition
                    def_parts = definition_str.split(/\s+/)
                    type = def_parts[0]

                    if type == 'Basic'
                        v_coeffs = def_parts[1..6].map(&:to_f)
                        u_coeffs = def_parts[7..12].map(&:to_f)
                        # Handle optional 7th u term (Qu) and f_formula logic matching libcongen
                        f_formula = 1
                        if def_parts.length > 14
                            u_coeffs << def_parts[13].to_f
                            f_formula = def_parts[14].to_i
                        else
                            u_coeffs << 0.0
                            f_formula = def_parts[13].to_i
                        end

                        @constituent_definitions[name] = {
                            'type' => 'Basic',
                            'v' => v_coeffs,
                            'u' => u_coeffs,
                            'f_formula' => f_formula,
                            'speed' => speed
                        }
                    elsif type == 'Compound'
                        coeffs = def_parts[1..].map(&:to_f)
                        @constituent_definitions[name] = {
                            'type' => 'Compound',
                            'coefficients' => coeffs,
                            'speed' => speed
                        }
                    end
                when :data_sets
                    # (index, name, station_id_context, station_id, lat, lng, timezone, country, units, ..., meridian, ...)
                    idx = parts[0]
                    stations_by_index[idx] = {
                        'name' => parts[1],
                        'lat' => parts[4].to_f,
                        'lng' => parts[5].to_f,
                        'timezone' => parts[6].sub(/^:/, ''), # Remove leading colon
                        'units' => parts[8],
                        'meridian' => (parts[18] == '\N' || parts[18].blank?) ? '00:00:00' : parts[18],
                        'datum_offset' => parts[20].to_f # datum column
                    }
                when :constants
                    # (index, name, phase, amp)
                    idx = parts[0]
                    constants_by_index[idx] << {
                        'name' => parts[1],
                        'phase' => parts[2].to_f,
                        'amp' => parts[3].to_f
                    }
                end
            end

            stations = []
            stations_by_index.each do |idx, data|
                station_id = "harm_#{idx}"
                station = {
                    'name' => data['name'],
                    'alternate_names' => [],
                    'id' => station_id,
                    'public_id' => station_id,
                    'region' => nil,
                    'location' => data['name'],
                    'lat' => data['lat'],
                    'lon' => data['lng'],
                    'timezone' => data['timezone'],
                    'url' => "#harmonics",
                    'provider' => 'harmonics',
                    'bid' => nil,
                    'units' => data['units'],
                    'depth' => data['datum_offset'],
                    'meridian' => data['meridian'],
                }
                stations << station
                @stations_cache[station_id] = {
                    'constituents' => constants_by_index[idx],
                    'datum_offset' => data['datum_offset'],
                    'timezone' => data['timezone'],
                    'meridian' => data['meridian'],
                    'units' => data['units']
                }
            end

            stations
        end

        def parse_ticon_file
            return [] unless File.exist?(@ticon_file)
            @logger.info "Loading TICON data from: #{@ticon_file}"

            begin
                data = JSON.parse(File.read(@ticon_file))

                stations = []
                data['stations'].each do |d|
                    id = d['id']

                    station = {
                        'name' => d['name'],
                        'alternate_names' => [],
                        'id' => id,
                        'public_id' => id,
                        'region' => nil,
                        'location' => d['name'],
                        'lat' => d['lat'],
                        'lon' => d['lon'],
                        'timezone' => d['timezone'],
                        'url' => "#ticon",
                        'provider' => 'ticon',
                        'bid' => nil,
                        'units' => d['units'],
                        'depth' => d['datum_offset'],
                        'meridian' => '00:00:00' # TICON data is UTC-based
                    }

                    stations << station
                    @stations_cache[id] = {
                        'constituents' => d['constituents'],
                        'datum_offset' => d['datum_offset'],
                        'timezone' => d['timezone'],
                        'meridian' => '00:00:00',
                        'units' => d['units']
                    }
                end

                @logger.info "Loaded #{stations.length} TICON stations from JSON."
                stations
            rescue => e
                @logger.error "Failed to parse TICON JSON: #{e.message}"
                []
            end
        end

        def get_nodal_factors(year, month = 7, day = 2, meridian_offset = 0.0)
            key = "#{year}_#{month}_#{day}_#{meridian_offset}"
            @nodal_factors_cache[key] ||= load_nodal_cache(year, month, day, meridian_offset) || begin
                factors = calculate_nodal_factors(year, month, day, meridian_offset)
                save_nodal_cache(year, month, day, meridian_offset, factors)
                factors
            end
        end

        def nodal_cache_file(year, month, day, meridian_offset)
            # Use safe filename for meridian (e.g. -5.0 -> _m5.0)
            m_str = meridian_offset.to_s.gsub('-', 'm')
            "#{@cache_dir}/nodal_factors_#{year}_#{month}_#{day}_#{m_str}.json"
        end

        def load_nodal_cache(year, month, day, meridian_offset)
            file = nodal_cache_file(year, month, day, meridian_offset)
            if File.exist?(file)
                @logger.debug "Loading nodal factors for #{year}-#{month}-#{day} (m:#{meridian_offset}) from cache"
                return JSON.parse(File.read(file))
            end
            nil
        end

        def save_nodal_cache(year, month, day, meridian_offset, factors)
            FileUtils.mkdir_p(@cache_dir)
            file = nodal_cache_file(year, month, day, meridian_offset)
            @logger.debug "Caching nodal factors for #{year}-#{month}-#{day} (m:#{meridian_offset}) to #{file}"
            File.write(file, factors.to_json)
        end

        def calculate_nodal_factors(year, month = 7, day = 2, meridian_offset = 0.0)
            @logger.info "Calculating astronomical nodal factors for #{year}-#{month}-#{day} (m:#{meridian_offset})..."

            # Use noon of the specific day as the representative epoch
            # Nodal factors are traditionally calculated for Local Standard Time midnight or noon.
            t_mid = Time.find_zone('UTC').local(year, month, day, 12, 0, 0) + meridian_offset.hours
            t_start = Time.find_zone('UTC').local(year, 1, 1, 0, 0, 0) + meridian_offset.hours

            # Fundamental arguments at start of year (for V0) and mid-day (for u and f)
            arg_start = astronomical_arguments(t_start)
            arg_mid = astronomical_arguments(t_mid)

            factors = {}

            # Pre-populate bases to ensure they are available for compound constituents
            BASES.each do |name, d|
                factors[name] = calculate_basic_factors(d, arg_start, arg_mid)
            end

            @constituent_definitions.each do |name, d|
                if d['type'] == 'Basic'
                    factors[name] = calculate_basic_factors(d, arg_start, arg_mid)
                elsif d['type'] == 'Compound'
                    factors[name] = calculate_compound_factors(d, factors)
                end
            end
            factors
        end

        private

        def parse_meridian(m)
            return 0.0 if m.blank? || m == '\N'
            sign = m.start_with?('-') ? -1 : 1
            parts = m.sub(/^-/, '').split(':').map(&:to_f)
            # Meridian offset is hours from UTC.
            # In XTide SQL, '05:00:00' for East Coast USA is often stored as '00:00:00' with timezone handle.
            # If a meridian IS present, we apply it.
            (parts[0] + (parts[1] || 0.0)/60.0 + (parts[2] || 0.0)/3600.0) * sign
        end

        def astronomical_arguments(t)
            # T = Julian centuries since 1899-12-31 12:00 UTC
            cap_t = (t - TABLE_1_EPOCH) / SECONDS_PER_JULIAN_CENTURY
            t2 = cap_t * cap_t
            t3 = t2 * cap_t

            # Exact high-precision SP 98 Table 1 formulas (matching libcongen.cc)
            s  = (270.0 + 26.0/60.0 + 14.72/3600.0) +
                 (1336.0 * 360.0 + 1108411.2/3600.0) * cap_t +
                 (9.09/3600.0) * t2 +
                 (0.0068/3600.0) * t3

            h  = (279.0 + 41.0/60.0 + 48.04/3600.0) +
                 (129602768.13/3600.0) * cap_t +
                 (1.089/3600.0) * t2

            p  = (334.0 + 19.0/60.0 + 40.87/3600.0) +
                 (11.0 * 360.0 + 392515.94/3600.0) * cap_t -
                 (37.24/3600.0) * t2 -
                 (0.045/3600.0) * t3

            p1 = (281.0 + 13.0/60.0 + 15.0/3600.0) +
                 (6189.03/3600.0) * cap_t +
                 (1.63/3600.0) * t2 +
                 (0.012/3600.0) * t3

            n  = (259.0 + 10.0/60.0 + 57.12/3600.0) -
                 (5.0 * 360.0 + 482912.63/3600.0) * cap_t +
                 (7.58/3600.0) * t2 +
                 (0.008/3600.0) * t3

            # tau (hour angle of mean sun)
            d = (t - TABLE_1_EPOCH) / 86400.0
            tau = (d * 360.0) % 360.0

            { 's' => s, 'h' => h, 'p' => p, 'p1' => p1, 'N' => n, 'tau' => tau }
        end

        def calculate_basic_factors(d, arg_start, arg_mid)
            v_coeffs = d['v'] # [T, s, h, p, p1, c]
            u_coeffs = d['u'] # [xi, nu, nu', 2nu'', Q, R, Qu]
            f_formula = d['f_formula']

            # V0 at start of year
            # V = T*tau + s*s + h*h + p*p + p1*p1 + c
            # Note: tau in definitions is the coefficient of T (hour angle)
            v0 = v_coeffs[0] * arg_start['tau'] +
                 v_coeffs[1] * arg_start['s'] +
                 v_coeffs[2] * arg_start['h'] +
                 v_coeffs[3] * arg_start['p'] +
                 v_coeffs[4] * arg_start['p1'] +
                 v_coeffs[5]

            # u and f at mid-year
            # Need derived arguments: I, xi, nu, etc.
            n = arg_mid['N']
            p = arg_mid['p']

            # Derived arguments from N (degrees)
            cos_i = cosd(OBLIQUITY) * cosd(LUNAR_INCLINATION) - sind(OBLIQUITY) * sind(LUNAR_INCLINATION) * cosd(n)
            cap_i = acosd(cos_i)
            sin_i = sind(cap_i)

            sin_nu = sind(LUNAR_INCLINATION) * sind(n) / sin_i
            nu = asind(sin_nu)

            sin_omega = sind(OBLIQUITY) * sind(n) / sin_i
            cos_omega = cosd(n) * cosd(nu) + sind(n) * sind(nu) * cosd(OBLIQUITY)
            xi = n - atan2d(sin_omega, cos_omega)

            nu_prime = atan2d(sind(2 * cap_i) * sind(nu), sind(2 * cap_i) * cosd(nu) + 0.3347)
            two_nu_double_prime = atan2d(sin_i * sin_i * sind(2 * nu), sin_i * sin_i * cosd(2 * nu) + 0.0727)

            big_p = p - xi
            q = atan2d(0.483 * sind(big_p), cosd(big_p))
            qu = big_p - q
            qa = 1.0 / Math.sqrt(2.31 + 1.435 * cosd(2 * big_p))

            # R and Ra for L2
            cot_i_2 = 1.0 / Math.tan(cap_i / 2.0 * Math::PI / 180.0)
            r_arg = atan2d(sind(2 * big_p), (cot_i_2 * cot_i_2 / 6.0) - cosd(2 * big_p))
            tan_i_2 = Math.tan(cap_i / 2.0 * Math::PI / 180.0)
            ra = 1.0 / Math.sqrt(1.0 - 12.0 * tan_i_2 * tan_i_2 * cosd(2 * big_p) + 36.0 * tan_i_2**4)

            # u terms vector
            u_terms = [xi, nu, nu_prime, two_nu_double_prime, q, r_arg, qu]
            u = 0.0
            u_coeffs.each_with_index { |c, i| u += c * u_terms[i] if i < u_terms.length }

            # Node factor f
            f = case f_formula
                when 1   then 1.0
                when 73  then (2.0/3.0 - sind(cap_i)**2) / 0.5021
                when 74  then sind(cap_i)**2 / 0.1578
                when 75  then sind(cap_i) * cosd(cap_i/2.0)**2 / 0.38
                when 76  then sind(2.0 * cap_i) / 0.7214
                when 77  then sind(cap_i) * sind(cap_i/2.0)**2 / 0.0164
                when 78  then cosd(cap_i/2.0)**4 / 0.9154
                when 79  then sind(cap_i)**2 / 0.1565
                when 144 then (1.0 - 10.0 * sind(cap_i/2.0)**2 + 15.0 * sind(cap_i/2.0)**4) * cosd(cap_i/2.0)**2 / 0.5873
                when 149 then cosd(cap_i/2.0)**6 / 0.8758
                when 206 then (sind(cap_i) * cosd(cap_i/2.0)**2 / 0.38) / qa
                when 215 then (cosd(cap_i/2.0)**4 / 0.9154) / ra
                when 227 then Math.sqrt(0.8965 * sind(2.0 * cap_i)**2 + 0.6001 * sind(2.0 * cap_i) * cosd(nu) + 0.1006)
                when 235 then Math.sqrt(19.0444 * sind(cap_i)**4 + 2.7702 * sind(cap_i)**2 * cosd(2.0 * nu) + 0.0981)
                else 1.0
                end

            { 'f' => f, 'u' => u, 'V0' => v0 }
        end

        def calculate_compound_factors(d, factors_so_far)
            coeffs = d['coefficients']
            # Compound constituents use the 13 bases

            f = 1.0
            u = 0.0
            v0 = 0.0

            coeffs.each_with_index do |c, i|
                next if c == 0.0 || i >= BASES_ORDER.length
                base_name = BASES_ORDER[i]
                base_f = factors_so_far[base_name]

                if base_f
                    f *= (base_f['f'] ** c.abs)
                    u += c * base_f['u']
                    v0 += c * base_f['V0']
                end
            end

            { 'f' => f, 'u' => u, 'V0' => v0 }
        end

        # Trig helpers in degrees
        def sind(deg) Math.sin(deg * Math::PI / 180.0) end
        def cosd(deg) Math.cos(deg * Math::PI / 180.0) end
        def tand(deg) Math.tan(deg * Math::PI / 180.0) end
        def asind(x)  Math.asin(x) * 180.0 / Math::PI end
        def acosd(x)  Math.acos(x) * 180.0 / Math::PI end
        def atan2d(y, x) Math.atan2(y, x) * 180.0 / Math::PI end
    end
end

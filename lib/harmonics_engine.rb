require 'json'
require 'fileutils'
require 'date'
require 'active_support/all'
require 'digest'

module Harmonics
    class Engine
        class MissingSourceFilesError < StandardError; end

        XTIDE_FILE = File.expand_path('../data/latest-xtide.sql', __dir__)
        TICON_FILE = File.expand_path('../data/latest-ticon.json', __dir__)

        attr_reader :speeds, :stations_cache, :xtide_file, :ticon_file, :logger

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
            @xtide_file = ENV['XTIDE_FILE'] || XTIDE_FILE
            @ticon_file = ENV['TICON_FILE'] || TICON_FILE
            @cache_dir = cache_dir || 'cache'
            @stations_cache = {}
            @speeds = {}
            @constituent_definitions = {}
            @nodal_factors_cache = {}
            @parsed_stations = nil
        end

        def stations
            ensure_source_files!
            @parsed_stations ||= load_stations_from_cache || begin
                xtide_stations = parse_xtide_file
                ticon_stations = parse_ticon_file
                merged = xtide_stations + ticon_stations

                # Deduplicate merged stations by proximity, name, and constituents
                deduplicated = deduplicate_stations(merged)

                save_stations_to_cache(deduplicated)
                deduplicated
            end
        end

        def ensure_source_files!
            return @files_checked ||= begin
                missing = []
                missing << "XTide (#{@xtide_file})" unless File.exist?(@xtide_file)
                missing << "TICON (#{@ticon_file})" unless File.exist?(@ticon_file)

                unless missing.empty?
                    raise MissingSourceFilesError, "Harmonics::Engine requires XTide and TICON data files. Missing: #{missing.join(', ')}. Set XTIDE_FILE/TICON_FILE or restore the data files."
                end

                true
            end
        end

        def find_station(id)
            # Ensure stations are loaded so @stations_cache is populated
            stations if @stations_cache.empty?

            # First look in the primary list (metadata)
            station = stations.find { |s| s['id'] == id || s['bid'] == id }
            return station if station

            # If not found, look in the cache for aliased IDs
            if data = @stations_cache[id]
                # Reconstruct metadata from cache data
                return {
                    'id' => id,
                    'name' => data['name'],
                    'region' => data['region'],
                    'timezone' => data['timezone'],
                    'units' => data['units'],
                    'type' => data['type'],
                    'provider' => 'xtide' # Defaulting to xtide if it was an alias
                }
            end
            nil
        end

        def generate_predictions(station_id, start_time, end_time, options = {})
            # Ensure stations are loaded so @stations_cache is populated
            stations if @stations_cache.empty?

            station_data = @stations_cache[station_id] || {}

            step_seconds = options.fetch(:step_seconds, 60).to_f

            # If this is a subordinate station, we predict for the reference station
            # and then apply offsets.
            if station_data['ref_key']
                ref_key = station_data['ref_key']
                @logger.debug "Station #{station_id} is subordinate to #{ref_key}. Predicting via ref station."

                # We need to predict a slightly larger window for the ref station to ensure
                # we don't miss peaks that shift into our requested window after offsets.
                # Max offset in XTide is usually around 12-24h but realistically 1-2h.
                # We'll add 2 hours buffer on both sides.
                ref_start = start_time - 2.hours
                ref_end = end_time + 2.hours

                ref_predictions = generate_predictions(ref_key, ref_start, ref_end, options)
                return apply_subordinate_offsets(ref_predictions, station_data, start_time, end_time, step_seconds: step_seconds)
            end

            constituents = station_data['constituents'] || []

            if constituents.empty?
                @logger.warn "No constituents found for station #{station_id}"
                return []
            end

            datum_offset = station_data['datum_offset'] || 0.0
            meridian_offset = parse_meridian(station_data['meridian'])
            if options.key?(:meridian_override)
                meridian_offset = options[:meridian_override].to_f
            elsif options[:meridian_from_timezone]
                meridian_offset = start_time.utc_offset / 3600.0
            end
            units = station_data['units'] || 'ft'
            nodal_hour = options.fetch(:nodal_hour, 12)

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
                    nodal = get_nodal_factors(current_utc.year, current_utc.month, current_utc.day, meridian_offset, nodal_hour)
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
                current_utc += step_seconds
            end

            predictions
        end

        def detect_peaks(predictions, step_seconds: 60)
            peaks = []
            return peaks if predictions.empty?

            # Special case for subordinate "predictions" which might already be peaks
            # if they came from apply_subordinate_offsets.
            # But generate_predictions is supposed to return a time series.
            # However, XTide subordinate logic is PEAK-BASED.
            # If predictions contains 'type', it's already a peak list.
            if predictions.first&.has_key?('type')
                return predictions
            end

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
                        offset_seconds = ((y1 - y3) / (2.0 * denom)) * step_seconds
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

        def apply_subordinate_offsets(ref_predictions, sub_data, start_time, end_time, step_seconds: 60)
            # detect_peaks on ref_predictions to get high/low times/heights
            ref_peaks = detect_peaks(ref_predictions, step_seconds: step_seconds)

            sub_peaks = ref_peaks.map do |rp|
                is_high = rp['type'] == 'High'

                time_offset_str = is_high ? sub_data['h_time_offset'] : sub_data['l_time_offset']
                height_mult = is_high ? sub_data['h_height_mult'] : sub_data['l_height_mult']

                # Apply time offset
                # Format is [+-]HH:MM:SS
                offset_seconds = 0
                if time_offset_str && time_offset_str != '\N'
                    sign = time_offset_str.start_with?('-') ? -1 : 1
                    parts = time_offset_str.delete('+-').split(':').map(&:to_i)
                    offset_seconds = sign * (parts[0] * 3600 + parts[1] * 60 + (parts[2] || 0))
                end

                new_time = rp['time'] + offset_seconds.seconds
                new_height = rp['height'] * height_mult

                {
                    'type' => rp['type'],
                    'time' => new_time,
                    'height' => new_height.round(3),
                    'units' => rp['units']
                }
            end

            # Filter to requested window
            sub_peaks.select { |p| p['time'] >= start_time && p['time'] <= end_time }
        end

        def deduplicate_stations(stations)
            # Group by normalized name (lowercase, alphanumeric only)
            groups = stations.group_by { |s| s['name'].downcase.gsub(/[^a-z0-9]/, '') }

            final_stations = []

            groups.each do |name_key, group_stations|
                # Further group by proximity (approx 5km tolerance)
                while group_stations.any?
                    primary = group_stations.shift

                    # Find all others in the group that are within ~5km (0.05 degrees)
                    near_matches = group_stations.select do |other|
                        (primary['lat'] - other['lat']).abs < 0.05 &&
                        (primary['lon'] - other['lon']).abs < 0.05
                    end

                    # Separate those with identical constituents from those with different ones
                    identical_matches = near_matches.select do |other|
                        key1 = primary['bid'] || primary['id']
                        key2 = other['bid'] || other['id']
                        constituents_equal?(key1, key2)
                    end
                    different_matches = near_matches - identical_matches

                    # Log if we found different predictive models for the same spot
                    different_matches.each do |other|
                        id1 = primary['bid'] || primary['id']
                        id2 = other['bid'] || other['id']
                        @logger.debug "Station cluster match [#{name_key}] at #{primary['lat']},#{primary['lon']} has different constituents: #{id1} vs #{id2}"
                    end

                    # For identical ones, we merge them into one entry
                    # Choose the best station from the identical cluster
                    cluster = [primary] + identical_matches
                    best = cluster.sort_by do |s|
                        # Priority: ticon > xtide
                        provider_rank = s['provider'] == 'ticon' ? 0 : 1
                        [provider_rank, s['name'].length, s['id']]
                    end.first

                    # Ensure all IDs from the cluster point to the same cache entry
                    # This preserves backward compatibility for merged stations.
                    best_key = best['bid'] || best['id']
                    best_data = @stations_cache[best_key]

                    cluster.each do |s|
                        key = s['bid'] || s['id']
                        next if key == best_key
                        @stations_cache[key] = best_data
                    end

                    # Remove merged identical matches from the pool
                    group_stations -= identical_matches

                    final_stations << best
                end
            end

            @logger.info "Deduplicated stations: #{stations.length} -> #{final_stations.length}"
            final_stations
        end

        def constituents_equal?(id1, id2)
            s1_data = @stations_cache[id1]
            s2_data = @stations_cache[id2]
            return false unless s1_data && s2_data

            c1 = s1_data['constituents'] || []
            c2 = s2_data['constituents'] || []

            return false if c1.length != c2.length
            return true if c1.empty? && c2.empty?

            # Sort for comparison
            s1_sorted = c1.sort_by { |c| c['name'] }
            s2_sorted = c2.sort_by { |c| c['name'] }

            # Factors for unit normalization (meters vs feet)
            # TICON is always meters. XTide is usually feet.
            f1 = (s1_data['units'] =~ /^m/i) ? 1.0 : 0.3048
            f2 = (s2_data['units'] =~ /^m/i) ? 1.0 : 0.3048

            s1_sorted.each_with_index do |con1, i|
                con2 = s2_sorted[i]
                return false if con1['name'] != con2['name']

                # Compare amplitudes in meters
                amp1 = con1['amp'] * f1
                amp2 = con2['amp'] * f2
                return false if (amp1 - amp2).abs > 0.005 # Tolerance for conversion rounding

                # Phases are in degrees
                return false if (con1['phase'] - con2['phase']).abs > 0.1 # Tolerance for slight variations
            end

            true
        end

        def load_stations_from_cache
            cache_file = "#{@cache_dir}/xtide_stations.json"

            # Check if cache is newer than BOTH source files
            sources = [@xtide_file, @ticon_file]

            # Get modification times, treating missing files as having very old times
            latest_source_time = sources.map do |f|
                File.exist?(f) ? File.mtime(f) : Time.new(1970, 1, 1)
            end.max

            if File.exist?(cache_file) && File.mtime(cache_file) > latest_source_time
                @logger.debug "Loading merged stations from cache: #{cache_file}"
                data = JSON.parse(File.read(cache_file))

                stations = []
                @stations_cache = data['stations_cache'] || {}
                @speeds = data['speeds'] || {}
                @constituent_definitions = data['constituent_definitions'] || {}

                data['stations'].each do |h|
                    stations << h['metadata']
                end
                return stations
            end
            nil
        end

        def save_stations_to_cache(stations)
            FileUtils.mkdir_p(@cache_dir)
            cache_file = "#{@cache_dir}/xtide_stations.json"
            @logger.debug "Caching xtide stations to: #{cache_file}"

            cache_data = {
                'speeds' => @speeds,
                'constituent_definitions' => @constituent_definitions,
                'stations_cache' => @stations_cache,
                'stations' => stations.map do |s|
                    cache_key = s['bid'] || s['id']
                    cache_entry = @stations_cache[cache_key]
                    {
                        'metadata' => s,
                        'name' => cache_entry['name'],
                        'constituents' => cache_entry['constituents'],
                        'datum_offset' => cache_entry['datum_offset'],
                        'timezone' => cache_entry['timezone'],
                        'meridian' => cache_entry['meridian'],
                        'units' => cache_entry['units'],
                        'region' => cache_entry['region'],
                        'type' => cache_entry['type'],
                        'ref_key' => cache_entry['ref_key'],
                        'h_time_offset' => cache_entry['h_time_offset'],
                        'h_height_mult' => cache_entry['h_height_mult'],
                        'l_time_offset' => cache_entry['l_time_offset'],
                        'l_height_mult' => cache_entry['l_height_mult']
                    }
                end
            }
            File.write(cache_file, cache_data.to_json)
        end

        def parse_xtide_file
            @logger.info "Parsing SQL XTide file: #{@xtide_file}"
            stations_data = []
            constants_by_index = Hash.new { |h, k| h[k] = [] }
            current_table = nil

            # Use ISO-8859-1 (Latin-1) encoding as specified in the SQL file
            File.foreach(@xtide_file, encoding: 'ISO-8859-1:UTF-8') do |line|
                line.strip!

                if line.start_with?('COPY public.constituents ')
                    current_table = :constituents
                    next
                elsif line.start_with?('COPY public.data_sets ')
                    current_table = :data_sets
                    next
                elsif line.start_with?('COPY public.constants ')
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
                    # (index, name, station_id_context, station_id, lat, lng, timezone, country, units, ..., meridian, ..., state)
                    idx = parts[0].to_i
                    stations_data << {
                        'idx' => idx,
                        'name' => parts[1],
                        'station_id' => parts[3],
                        'lat' => parts[4].to_f,
                        'lng' => parts[5].to_f,
                        'timezone' => parts[6].sub(/^:/, ''),
                        'region' => parts[7],
                        'units' => parts[8],
                        'meridian' => parts[18],
                        'datum_offset' => parts[20].to_f,
                        'ref_index' => parts[23] == '\N' ? nil : parts[23].to_i,
                        'h_time_offset' => parts[24] == '\N' ? nil : parts[24],
                        'h_height_mult' => parts[26] == '\N' ? 1.0 : parts[26].to_f,
                        'l_time_offset' => parts[27] == '\N' ? nil : parts[27],
                        'l_height_mult' => parts[29] == '\N' ? 1.0 : parts[29].to_f,
                        'type' => parts[8].downcase == 'knots' ? 'current' : 'tide'
                    }
                when :constants
                    # (index, name, phase, amp)
                    data_set_id = parts[0].to_i
                    name = parts[1]
                    phase = parts[2].to_f
                    amp = parts[3].to_f
                    constants_by_index[data_set_id] << { 'name' => name, 'amp' => amp, 'phase' => phase }
                end
            end

            stations = []
            stations_data.each do |data|
                idx = data['idx']

                # Generate stable ID based on coordinates to match legacy X-IDs
                coord_string = sprintf("%.8f_%.8f", data['lat'], data['lng'])
                base_hash = Digest::SHA256.hexdigest(coord_string)[0...7]
                base_id = "X#{base_hash}"

                # Handle depth and BID for currents
                depth = data['datum_offset']
                station_bid = nil
                cache_key = base_id

                if data['type'] == 'current'
                    depth_suffix = nil
                    if data['name'] =~ /\(depth (\d+)\s*(ft|m)\)/i
                        depth_suffix = $1
                        depth = $1.to_f
                    end
                    station_bid = depth_suffix ? "#{base_id}_#{depth_suffix}" : base_id
                    cache_key = station_bid
                else
                    station_bid = nil
                    cache_key = base_id
                end

                # If this is a subordinate station, store its offsets and the reference station's cache key
                ref_key = nil
                if data['ref_index']
                    ref_station = stations_data.find { |s| s['idx'] == data['ref_index'] }
                    if ref_station
                        ref_coord_string = sprintf("%.8f_%.8f", ref_station['lat'], ref_station['lng'])
                        ref_base_hash = Digest::SHA256.hexdigest(ref_coord_string)[0...7]
                        ref_key = "X#{ref_base_hash}"
                    end
                end

                # If this is a subordinate station, copy constituents from the reference station if it has them
                # OR we might need to handle it purely via ref-station prediction later.
                # For now, let's keep the constituent copying but also store the ref_key and offsets.
                constituents = if data['ref_index']
                                   constants_by_index[data['ref_index']] || []
                               else
                                   constants_by_index[idx] || []
                               end

                if constituents.empty?
                    @logger.warn "Station #{data['name']} (#{idx}) has no constituents (ref_index: #{data['ref_index']})"
                end

                station = {
                    'name' => data['name'],
                    'alternate_names' => [],
                    'id' => base_id,
                    'public_id' => base_id,
                    'region' => data['region'],
                    'location' => data['name'],
                    'lat' => data['lat'],
                    'lon' => data['lng'],
                    'timezone' => data['timezone'],
                    'url' => "#xtide",
                    'provider' => 'xtide',
                    'bid' => station_bid,
                    'units' => data['units'],
                    'depth' => depth,
                    'meridian' => data['meridian'],
                    'type' => data['type']
                }
                stations << station
                @stations_cache[cache_key] = {
                    'name' => data['name'],
                    'constituents' => constituents,
                    'datum_offset' => data['datum_offset'],
                    'timezone' => data['timezone'],
                    'meridian' => data['meridian'],
                    'units' => data['units'],
                    'region' => data['region'],
                    'type' => data['type'],
                    'ref_key' => ref_key,
                    'h_time_offset' => data['h_time_offset'],
                    'h_height_mult' => data['h_height_mult'],
                    'l_time_offset' => data['l_time_offset'],
                    'l_height_mult' => data['l_height_mult']
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
                    # TICON base ID is coordinate-based
                    coord_string = sprintf("%.8f_%.8f", d['lat'], d['lon'])
                    base_hash = Digest::SHA256.hexdigest(coord_string)[0...7]
                    base_id = "T#{base_hash}"

                    type = d['units'].downcase == 'knots' ? 'current' : 'tide'

                    # Extract depth from name if available
                    depth = d['datum_offset']
                    depth_suffix = nil
                    if d['name'] =~ /\(depth (\d+)\s*(ft|m)\)/i
                        depth = $1.to_f
                        depth_suffix = $1
                    end

                    # For currents, the unique key is the BID (base_id + depth suffix)
                    if type == 'current'
                        station_bid = depth_suffix ? "#{base_id}_#{depth_suffix}" : base_id
                        cache_key = station_bid
                    else
                        station_bid = nil
                        cache_key = base_id
                    end

                    station = {
                        'name' => d['name'],
                        'alternate_names' => [],
                        'id' => base_id,
                        'public_id' => base_id,
                        'region' => d['region'],
                        'location' => d['name'],
                        'lat' => d['lat'],
                        'lon' => d['lon'],
                        'timezone' => d['timezone'],
                        'url' => "#ticon",
                        'provider' => 'ticon',
                        'bid' => station_bid,
                        'units' => d['units'],
                        'depth' => depth,
                        'meridian' => '00:00:00', # TICON data is UTC-based
                        'type' => type
                    }

                    stations << station
                    @stations_cache[cache_key] = {
                        'name' => d['name'],
                        'constituents' => d['constituents'],
                        'datum_offset' => d['datum_offset'],
                        'timezone' => d['timezone'],
                        'meridian' => '00:00:00',
                        'units' => d['units'],
                        'region' => d['region'],
                        'type' => type
                    }
                end

                @logger.info "Loaded #{stations.length} TICON stations from JSON."
                stations
            rescue => e
                @logger.error "Failed to parse TICON JSON: #{e.message}"
                []
            end
        end

        def get_nodal_factors(year, month = 7, day = 2, meridian_offset = 0.0, nodal_hour = 12)
            key = "#{year}_#{month}_#{day}_#{meridian_offset}_#{nodal_hour}"
            @nodal_factors_cache[key] ||= load_nodal_cache(year, month, day, meridian_offset, nodal_hour) || begin
                factors = calculate_nodal_factors(year, month, day, meridian_offset, nodal_hour)
                save_nodal_cache(year, month, day, meridian_offset, nodal_hour, factors)
                factors
            end
        end

        def nodal_cache_file(year, month, day, meridian_offset, nodal_hour)
            # Use safe filename for meridian (e.g. -5.0 -> _m5.0)
            m_str = meridian_offset.to_s.gsub('-', 'm')
            "#{@cache_dir}/nodal_factors_#{year}_#{month}_#{day}_#{m_str}_h#{nodal_hour}.json"
        end

        def load_nodal_cache(year, month, day, meridian_offset, nodal_hour)
            file = nodal_cache_file(year, month, day, meridian_offset, nodal_hour)
            if File.exist?(file)
                @logger.debug "Loading nodal factors for #{year}-#{month}-#{day} (m:#{meridian_offset}, h:#{nodal_hour}) from cache"
                return JSON.parse(File.read(file))
            end
            nil
        end

        def save_nodal_cache(year, month, day, meridian_offset, nodal_hour, factors)
            FileUtils.mkdir_p(@cache_dir)
            file = nodal_cache_file(year, month, day, meridian_offset, nodal_hour)
            @logger.debug "Caching nodal factors for #{year}-#{month}-#{day} (m:#{meridian_offset}, h:#{nodal_hour}) to #{file}"
            File.write(file, factors.to_json)
        end

        def calculate_nodal_factors(year, month = 7, day = 2, meridian_offset = 0.0, nodal_hour = 12)
            @logger.info "Calculating astronomical nodal factors for #{year}-#{month}-#{day} (m:#{meridian_offset}, h:#{nodal_hour})..."

            # Use noon of the specific day as the representative epoch
            # Nodal factors are traditionally calculated for Local Standard Time midnight or noon.
            t_mid = Time.find_zone('UTC').local(year, month, day, nodal_hour, 0, 0) + meridian_offset.hours
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

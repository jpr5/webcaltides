require 'json'
require 'fileutils'
require 'date'
require 'active_support/all'
require 'digest'
require 'tcd'

module Harmonics
    class Engine
        class MissingSourceFilesError < StandardError; end

        XTIDE_FILE = File.expand_path('../data/latest-xtide.tcd', __dir__)
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
            @logged_nodal_months = {}
            @parsed_stations = nil
            @reference_peaks_cache = {}
        end

        def stations
            ensure_source_files!
            # Double-checked locking for thread safety
            return @parsed_stations if @parsed_stations

            (@stations_mutex ||= Mutex.new).synchronize do
                return @parsed_stations if @parsed_stations

                @parsed_stations = load_stations_from_cache || begin
                    xtide_stations = parse_xtide_file
                    ticon_stations = parse_ticon_file
                    merged = xtide_stations + ticon_stations

                    # Deduplicate merged stations by proximity, name, and constituents
                    deduplicated = deduplicate_stations(merged)

                    save_stations_to_cache(deduplicated)
                    deduplicated
                end
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

        # Generate checksums for source files to version the cache.
        # Returns "xtidehash_ticonhash" (8 hex chars each).
        def source_files_checksum
            [@xtide_file, @ticon_file].map do |f|
                if File.exist?(f)
                    # Follow symlinks and hash the actual content
                    Digest::MD5.file(f).hexdigest[0, 8]
                else
                    "00000000"
                end
            end.join("_")
        end

        # Cache version - increment when cache format changes to force regeneration
        CACHE_VERSION = 2

        def stations_cache_file
            "#{@cache_dir}/xtide_stations_v#{CACHE_VERSION}_#{source_files_checksum}.json"
        end

        # Remove old station cache files that don't match current checksums.
        def cleanup_old_station_caches
            current = stations_cache_file
            Dir.glob("#{@cache_dir}/xtide_stations_*.json").each do |f|
                next if f == current
                @logger.info "removing old station cache: #{f}"
                File.unlink(f)
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
                @logger.debug "station #{station_id} is subordinate to #{ref_key}, predicting via ref station"

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
                @logger.warn "no constituents found for station #{station_id}"
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

        # Simple peak detection without parabolic refinement - for coarse pass
        def detect_approximate_peaks(predictions)
            return [] if predictions.length < 3

            peaks = []
            (1...predictions.length - 1).each do |i|
                prev_h = predictions[i-1]['height']
                curr_h = predictions[i]['height']
                next_h = predictions[i+1]['height']

                if curr_h > prev_h && curr_h > next_h
                    peaks << { 'time' => predictions[i]['time'], 'type' => 'High', 'height' => curr_h, 'units' => predictions[i]['units'] }
                elsif curr_h < prev_h && curr_h < next_h
                    peaks << { 'time' => predictions[i]['time'], 'type' => 'Low', 'height' => curr_h, 'units' => predictions[i]['units'] }
                end
            end
            peaks
        end

        # Optimized peak generation using coarse-to-fine approach
        # Instead of minute-by-minute for 13 months (571,200 points), we:
        # 1. Coarse pass at 15-min resolution (~37,440 points) to find approximate peaks
        # 2. Fine pass at 1-min resolution only around each peak (+/- 30 min = 60 points each)
        # Result: ~40,000 points instead of 571,200 = 93% reduction
        def generate_peaks_optimized(station_id, start_time, end_time, options = {})
            # Ensure stations are loaded so @stations_cache is populated
            stations if @stations_cache.empty?

            station_data = @stations_cache[station_id] || {}

            # Handle subordinate stations - use cached reference peaks
            if station_data['ref_key']
                return generate_subordinate_peaks_optimized(station_id, station_data, start_time, end_time, options)
            end

            constituents = station_data['constituents'] || []
            if constituents.empty?
                @logger.warn "no constituents found for station #{station_id}"
                return []
            end

            # Phase 1: Coarse detection at 15-minute intervals
            coarse_predictions = generate_predictions(station_id, start_time, end_time,
                                                      options.merge(step_seconds: 900))
            approximate_peaks = detect_approximate_peaks(coarse_predictions)

            return [] if approximate_peaks.empty?

            # Phase 2: Refine each peak with 1-minute resolution in a +/- 30 minute window
            approximate_peaks.map do |approx|
                window_start = approx['time'] - 30.minutes
                window_end = approx['time'] + 30.minutes

                fine_predictions = generate_predictions(station_id, window_start, window_end,
                                                        options.merge(step_seconds: 60))
                refined_peaks = detect_peaks(fine_predictions, step_seconds: 60)

                # Find the peak closest to our approximate time (should be exactly one)
                refined_peaks.min_by { |p| (p['time'] - approx['time']).abs }
            end.compact
        end

        private

        # Optimized subordinate peak generation with reference station caching
        def generate_subordinate_peaks_optimized(station_id, station_data, start_time, end_time, options)
            ref_key = station_data['ref_key']
            @logger.debug "station #{station_id} is subordinate to #{ref_key}, predicting via cached ref peaks"

            # Normalize window to month boundaries for consistent cache keys
            # Add 1 month buffer on each side to handle subordinate time offsets
            ref_start = start_time.beginning_of_month - 1.month
            ref_end = end_time.end_of_month + 1.month

            # Cache key uses normalized month boundaries (YYYYMM format)
            cache_key = "#{ref_key}:#{ref_start.strftime('%Y%m')}:#{ref_end.strftime('%Y%m')}"

            # Prune stale cache entries (older than current window)
            prune_reference_peaks_cache(ref_start)

            ref_peaks = @reference_peaks_cache[cache_key] ||= begin
                @logger.debug "generating reference peaks for #{ref_key} (caching for subordinates)"
                generate_peaks_optimized(ref_key, ref_start, ref_end, options)
            end

            # Apply subordinate offsets to the cached reference peaks
            apply_peak_offsets(ref_peaks, station_data, start_time, end_time)
        end

        # Remove cache entries for windows that end before the cutoff date
        def prune_reference_peaks_cache(cutoff)
            @reference_peaks_cache.delete_if do |key, _|
                # Key format: "ref_key:YYYYMM:YYYYMM"
                end_month = key.split(':').last
                end_month < cutoff.strftime('%Y%m')
            end
        end

        # Apply time and height offsets to reference peaks for subordinate stations
        def apply_peak_offsets(ref_peaks, sub_data, start_time, end_time)
            ref_peaks.filter_map do |rp|
                is_high = rp['type'] == 'High'

                time_offset_str = is_high ? sub_data['h_time_offset'] : sub_data['l_time_offset']
                height_mult = is_high ? sub_data['h_height_mult'] : sub_data['l_height_mult']

                # Apply time offset (format is [+-]HH:MM:SS)
                offset_seconds = 0
                if time_offset_str && time_offset_str != '\N'
                    sign = time_offset_str.start_with?('-') ? -1 : 1
                    parts = time_offset_str.delete('+-').split(':').map(&:to_i)
                    offset_seconds = sign * (parts[0] * 3600 + parts[1] * 60 + (parts[2] || 0))
                end

                new_time = rp['time'] + offset_seconds.seconds
                new_height = rp['height'] * height_mult

                # Filter to requested window
                next unless new_time >= start_time && new_time <= end_time

                {
                    'type' => rp['type'],
                    'time' => new_time,
                    'height' => new_height.round(3),
                    'units' => rp['units']
                }
            end
        end

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
                        @logger.debug "station cluster match [#{name_key}] at #{primary['lat']},#{primary['lon']} has different constituents: #{id1} vs #{id2}"
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

            @logger.info "deduplicated stations: #{stations.length} -> #{final_stations.length}"
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
            cache_file = stations_cache_file
            return nil unless File.exist?(cache_file)

            @logger.debug "loading merged stations from cache: #{cache_file}"
            data = JSON.parse(File.read(cache_file))

            stations = []
            @stations_cache = data['stations_cache'] || {}
            @speeds = data['speeds'] || {}
            @constituent_definitions = data['constituent_definitions'] || {}

            data['stations'].each do |h|
                stations << h['metadata']
            end
            stations
        end

        def save_stations_to_cache(stations)
            FileUtils.mkdir_p(@cache_dir)
            cache_file = stations_cache_file
            @logger.debug "caching xtide stations to: #{cache_file}"

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
                        'state' => cache_entry['state'],
                        'country' => cache_entry['country'],
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
            @logger.info "parsing TCD file: #{@xtide_file}"

            stations = []
            all_tcd_stations = []

            TCD.open(@xtide_file) do |db|
                @logger.info "TCD file opened: #{db.station_count} stations, #{db.constituent_count} constituents"

                # Load constituent speeds and definitions
                const_names = []
                db.constituents.each do |const|
                    const_names << const.name
                    @speeds[const.name] = const.speed

                    # If constituent exists in BASES, copy v/u arrays from there
                    # Otherwise create a basic definition with just speed and f_formula
                    if BASES[const.name]
                        # Copy the full definition from BASES (includes v, u, f_formula)
                        @constituent_definitions[const.name] = BASES[const.name].dup
                        # Override speed with TCD value in case it's more precise
                        @constituent_definitions[const.name]['speed'] = const.speed
                    else
                        # For non-BASES constituents, create basic definition
                        # These will not be used for nodal factor calculations
                        f_formula = map_constituent_to_formula(const.name)
                        @constituent_definitions[const.name] = {
                            'type' => 'Basic',
                            'speed' => const.speed,
                            'f_formula' => f_formula
                        }
                    end
                end

                # Store all stations first for reference lookups
                all_tcd_stations = db.stations.to_a

                # Process each station
                all_tcd_stations.each_with_index do |tcd_station, idx|
                    # Extract state from country if present (e.g., "United States" might have state in name)
                    state = extract_state_from_name(tcd_station.name)
                    country = tcd_station.country || "Unknown"

                    # Build region
                    state_full = state ? STATE_NAMES[state.upcase] : nil
                    region = state_full ? "#{state_full}, #{country}" : (state ? "#{state}, #{country}" : country)

                    # Convert zone_offset from HHMM integer to string (e.g., -500 -> "-05:00:00")
                    meridian = format_zone_offset(tcd_station.zone_offset)

                    # Determine station type
                    station_type = tcd_station.tide? ? 'tide' : 'current'
                    units = tcd_station.level_units || 'feet'

                    # Build constituents array for reference stations
                    constituents = []
                    if tcd_station.reference?
                        tcd_station.amplitudes.each_with_index do |amp, i|
                            next if amp.nil? || amp.zero?
                            constituents << {
                                'name' => const_names[i],
                                'amp' => amp,
                                'phase' => tcd_station.epochs[i]
                            }
                        end
                    end

                    # Generate stable ID based on coordinates
                    coord_string = sprintf("%.8f_%.8f", tcd_station.latitude, tcd_station.longitude)
                    base_hash = Digest::SHA256.hexdigest(coord_string)[0...7]
                    base_id = "X#{base_hash}"

                    # Handle depth and BID for currents
                    depth = tcd_station.datum_offset || 0.0
                    station_bid = nil
                    cache_key = base_id

                    if station_type == 'current'
                        depth_suffix = nil
                        if tcd_station.name =~ /\(depth (\d+)\s*(ft|m)\)/i
                            depth_suffix = $1
                            depth = $1.to_f
                        end
                        station_bid = depth_suffix ? "#{base_id}_#{depth_suffix}" : base_id
                        cache_key = station_bid
                    else
                        station_bid = nil
                        cache_key = base_id
                    end

                    # Handle subordinate station references
                    ref_key = nil
                    h_time_offset = nil
                    l_time_offset = nil
                    h_height_mult = 1.0
                    l_height_mult = 1.0

                    if tcd_station.subordinate?
                        ref_station = all_tcd_stations[tcd_station.reference_station]
                        if ref_station
                            ref_coord_string = sprintf("%.8f_%.8f", ref_station.latitude, ref_station.longitude)
                            ref_base_hash = Digest::SHA256.hexdigest(ref_coord_string)[0...7]
                            ref_base_id = "X#{ref_base_hash}"

                            # For current stations, include depth suffix
                            if ref_station.current? && ref_station.name =~ /\(depth (\d+)\s*(ft|m)\)/i
                                ref_key = "#{ref_base_id}_#{$1}"
                            else
                                ref_key = ref_base_id
                            end
                        end

                        # Convert time offsets from minutes to "HH:MM:SS" format
                        h_time_offset = format_minutes_offset(tcd_station.max_time_add)
                        l_time_offset = format_minutes_offset(tcd_station.min_time_add)
                        h_height_mult = tcd_station.max_level_multiply || 1.0
                        l_height_mult = tcd_station.min_level_multiply || 1.0
                    end

                    if constituents.empty? && tcd_station.reference?
                        @logger.warn "reference station #{tcd_station.name} (#{idx}) has no constituents"
                    end

                    # Clean up display name
                    display_name = clean_station_name(tcd_station.name, station_type, state)

                    station = {
                        'name' => display_name,
                        'alternate_names' => [],
                        'id' => base_id,
                        'public_id' => base_id,
                        'region' => region,
                        'state' => state,
                        'country' => country,
                        'location' => tcd_station.name,
                        'lat' => tcd_station.latitude,
                        'lon' => tcd_station.longitude,
                        'timezone' => tcd_station.tzfile,
                        'url' => "#xtide",
                        'provider' => 'xtide',
                        'bid' => station_bid,
                        'units' => units,
                        'depth' => depth,
                        'meridian' => meridian,
                        'type' => station_type
                    }
                    stations << station

                    @stations_cache[cache_key] = {
                        'name' => tcd_station.name,
                        'constituents' => constituents,
                        'datum_offset' => depth,
                        'timezone' => tcd_station.tzfile,
                        'meridian' => meridian,
                        'units' => units,
                        'region' => region,
                        'state' => state,
                        'country' => country,
                        'type' => station_type,
                        'ref_key' => ref_key,
                        'h_time_offset' => h_time_offset,
                        'h_height_mult' => h_height_mult,
                        'l_time_offset' => l_time_offset,
                        'l_height_mult' => l_height_mult,
                        'latitude' => tcd_station.latitude,
                        'longitude' => tcd_station.longitude
                    }
                end
            end

            @logger.info "Loaded #{@speeds.size} constituents, #{stations.size} stations from TCD"
            stations
        end

        # State abbreviation to full name mapping for cleaning station names
        STATE_NAMES = {
            'AL' => 'Alabama', 'AK' => 'Alaska', 'AZ' => 'Arizona', 'AR' => 'Arkansas',
            'CA' => 'California', 'CO' => 'Colorado', 'CT' => 'Connecticut', 'DE' => 'Delaware',
            'FL' => 'Florida', 'GA' => 'Georgia', 'HI' => 'Hawaii', 'ID' => 'Idaho',
            'IL' => 'Illinois', 'IN' => 'Indiana', 'IA' => 'Iowa', 'KS' => 'Kansas',
            'KY' => 'Kentucky', 'LA' => 'Louisiana', 'ME' => 'Maine', 'MD' => 'Maryland',
            'MA' => 'Massachusetts', 'MI' => 'Michigan', 'MN' => 'Minnesota', 'MS' => 'Mississippi',
            'MO' => 'Missouri', 'MT' => 'Montana', 'NE' => 'Nebraska', 'NV' => 'Nevada',
            'NH' => 'New Hampshire', 'NJ' => 'New Jersey', 'NM' => 'New Mexico', 'NY' => 'New York',
            'NC' => 'North Carolina', 'ND' => 'North Dakota', 'OH' => 'Ohio', 'OK' => 'Oklahoma',
            'OR' => 'Oregon', 'PA' => 'Pennsylvania', 'RI' => 'Rhode Island', 'SC' => 'South Carolina',
            'SD' => 'South Dakota', 'TN' => 'Tennessee', 'TX' => 'Texas', 'UT' => 'Utah',
            'VT' => 'Vermont', 'VA' => 'Virginia', 'WA' => 'Washington', 'WV' => 'West Virginia',
            'WI' => 'Wisconsin', 'WY' => 'Wyoming', 'DC' => 'District of Columbia',
            'PR' => 'Puerto Rico', 'VI' => 'Virgin Islands', 'GU' => 'Guam',
            'AS' => 'American Samoa', 'MP' => 'Northern Mariana Islands'
        }.freeze

        # Helper methods for TCD parsing

        # Map constituent name to SP 98 formula number
        def map_constituent_to_formula(name)
            # Use existing BASES mapping if available
            return BASES[name]['f_formula'] if BASES[name]

            # Default formulas for common constituents not in BASES
            formula_map = {
                'SA' => 1, '2SM' => 78, 'MSF' => 78, 'MF' => 74, 'MM' => 73,
                '2Q1' => 75, 'SIGMA1' => 75, 'RHO1' => 75, 'M11' => 76,
                'M12' => 78, 'CHI1' => 75, 'PI1' => 1, 'PHI1' => 75,
                'THETA1' => 75, 'J1' => 76, 'OO1' => 77, '2MK3' => 149,
                'M3' => 149, 'MK3' => 149, 'MN4' => 78, 'MS4' => 78,
                'MK4' => 78, 'SN4' => 78, 'S4' => 1, 'SK4' => 78,
                '2MN6' => 78, 'M6' => 78, '2MS6' => 78, '2MK6' => 78,
                '2SM6' => 78, 'MSK6' => 78
            }
            formula_map[name] || 1  # Default to formula 1
        end

        # Convert TCD zone_offset (HHMM integer) to "HH:MM:SS" string
        def format_zone_offset(hhmm_int)
            return "00:00:00" if hhmm_int.nil? || hhmm_int.zero?

            sign = hhmm_int < 0 ? '-' : '+'
            abs_val = hhmm_int.abs
            hours = abs_val / 100
            minutes = abs_val % 100

            "#{sign}%02d:%02d:00" % [hours, minutes]
        end

        # Convert minutes to "HH:MM:SS" string
        def format_minutes_offset(minutes)
            return nil if minutes.nil?
            return "+00:00:00" if minutes.zero?

            sign = minutes < 0 ? '-' : '+'
            abs_min = minutes.abs
            hours = abs_min / 60
            mins = abs_min % 60

            "#{sign}%02d:%02d:00" % [hours, mins]
        end

        # Extract state abbreviation from station name if present
        def extract_state_from_name(name)
            # Look for state patterns at the end of name
            STATE_NAMES.each do |abbrev, full_name|
                return abbrev if name =~ /,\s*#{Regexp.escape(full_name)}$/i
                return abbrev if name =~ /,\s*#{abbrev}$/i
            end
            nil
        end

        # Clean up station display name by removing redundant suffixes and state names
        # that are already captured in the region field.
        # Examples:
        #   "Little Misery Island (depth 50 ft), Salem Sound, Massachusetts Current" -> "Little Misery Island (depth 50 ft), Salem Sound"
        #   "Portland, Casco Bay, Maine" -> "Portland, Casco Bay"
        def clean_station_name(name, type, state_abbrev)
            clean = name.dup

            # For currents, remove " Current" suffix
            clean = clean.sub(/ Current$/i, '') if type == 'current'

            # Remove trailing state name if it matches the state field
            if state_abbrev
                state_full = STATE_NAMES[state_abbrev.upcase]
                if state_full
                    # Remove ", StateName" from the end
                    clean = clean.sub(/,\s*#{Regexp.escape(state_full)}$/i, '')
                end
            end

            clean.strip
        end

        def parse_ticon_file
            return [] unless File.exist?(@ticon_file)
            @logger.info "loading TICON data from: #{@ticon_file}"

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

                @logger.info "loaded #{stations.length} TICON stations from JSON"
                stations
            rescue => e
                @logger.error "failed to parse TICON JSON: #{e.message}"
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
                # Suppress per-day logging - too verbose
                return JSON.parse(File.read(file))
            end
            nil
        end

        def save_nodal_cache(year, month, day, meridian_offset, nodal_hour, factors)
            FileUtils.mkdir_p(@cache_dir)
            file = nodal_cache_file(year, month, day, meridian_offset, nodal_hour)
            # Suppress per-day logging - too verbose
            File.write(file, factors.to_json)
        end

        def calculate_nodal_factors(year, month = 7, day = 2, meridian_offset = 0.0, nodal_hour = 12)
            # Only log once per month to reduce verbosity
            month_key = "#{year}-#{month}_#{meridian_offset}_#{nodal_hour}"
            unless @logged_nodal_months[month_key]
                @logger.info "calculating nodal factors for #{year}-#{month} (m:#{meridian_offset}, h:#{nodal_hour})"
                @logged_nodal_months[month_key] = true
            end

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
                if d['type'] == 'Basic' && d['v'] && d['u']
                    # Only calculate factors for Basic constituents that have v/u arrays
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

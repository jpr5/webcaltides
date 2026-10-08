require_relative 'base'
require_relative '../models/station'
require_relative '../models/tide_data'
require_relative '../lib/harmonics_engine'

module Clients
    class Harmonics < Base
        include TimeWindow

        attr_reader :engine

        # XTide usually covers a full year or more, but we'll stick to the window pattern
        self.window_size = 13.months

        def initialize(logger)
            super(logger)
            @engine = ::Harmonics::Engine.new(logger, WebCalTides.settings.cache_dir)
        end

        def tide_stations
            return [] unless File.exist?(@engine.xtide_file) || File.exist?(@engine.ticon_file)

            @engine.stations.select { |s| s['type'] == 'tide' }.map { |s| Models::Station.from_hash(s.stringify_keys) }
        end

        def current_stations
            return [] unless File.exist?(@engine.xtide_file) || File.exist?(@engine.ticon_file)

            @engine.stations.select { |s| s['type'] == 'current' }.map { |s| Models::Station.from_hash(s.stringify_keys) }
        end

        def current_data_for(station, around)
            # For currents, we need peaks (flood/ebb) AND zero crossings (slack)
            start_time = beginning_of_window(around)
            end_time = end_of_window(around)

            # Use bid if available (e.g. for currents with different depths), fallback to id.
            # A tide can have the same id (see Engine#cache_entry), so look among currents only.
            lookup_id = station.bid || station.id
            station_data = @engine.station_data(lookup_id, 'current')

            if station_data['ref_key']
                # Subordinate station: one reference series gives both its peaks
                # and its slacks.
                ref_predictions = @engine.reference_predictions(station_data, start_time, end_time)
                peaks = @engine.subordinate_peaks(ref_predictions, station_data, start_time, end_time)
                return [] if peaks.empty?

                slacks = subordinate_slack_waters(lookup_id, station_data, ref_predictions, peaks, start_time, end_time)
            else
                predictions = @engine.generate_predictions(lookup_id, start_time, end_time, type: 'current')
                return [] if predictions.empty?

                # Peaks are the maxima and minima of the signed velocity, and
                # slack water is its zero crossings.
                peaks = @engine.detect_peaks(predictions)
                slacks = detect_zero_crossings(predictions)
            end

            # Combine peaks and slacks, convert to CurrentData
            events = []

            peaks.each do |p|
                type = max_current_type(p)
                next unless type

                events << Models::CurrentData.new(
                    type: type,
                    time: p['time'].to_datetime,
                    velocity_major: p['height'],
                    depth: station.depth,
                    url: "#xtide"
                )
            end

            slacks.each do |s|
                events << Models::CurrentData.new(
                    type: 'slack',
                    time: s['time'].to_datetime,
                    velocity_major: 0.0,
                    depth: station.depth,
                    url: "#xtide"
                )
            end

            events.sort_by(&:time)
        end

        # Velocity is signed: flood is positive, ebb is negative.  A maximum
        # above zero is max flood and a minimum below zero is max ebb.  A maximum
        # below zero (weakest ebb) or a minimum above zero (weakest flood) is not
        # a max current, and gives nil.
        def max_current_type(peak)
            if peak['type'] == 'High' && peak['height'] > 0
                'flood'
            elsif peak['type'] == 'Low' && peak['height'] < 0
                'ebb'
            end
        end

        # Slack water times for a subordinate current station: the reference
        # station's zero crossings, moved by the subordinate's flood-begins and
        # ebb-begins time offsets.  ref_predictions must cover the window plus
        # Engine#subordinate_margin (Engine#reference_predictions does).
        #
        # Without both offsets, fall back to interpolating between the
        # subordinate's peaks, which is less accurate.  Every subordinate current
        # in the 2025-12-28 TCD has both, so this is a safeguard.
        def subordinate_slack_waters(lookup_id, station_data, ref_predictions, peaks, start_time, end_time)
            flood_begins = station_data['flood_begins']
            ebb_begins = station_data['ebb_begins']

            unless flood_begins && ebb_begins
                logger.warn "subordinate current #{lookup_id} has no flood_begins/ebb_begins offset " \
                             "(flood_begins=#{flood_begins.inspect}, ebb_begins=#{ebb_begins.inspect}); " \
                             "interpolating slack between peaks"
                return detect_zero_crossings(peaks)
            end

            detect_zero_crossings(ref_predictions).filter_map do |c|
                offset = c['begins'] == 'flood' ? flood_begins : ebb_begins
                time = c['time'] + @engine.offset_seconds(offset)
                next unless time >= start_time && time <= end_time

                c.merge('time' => time)
            end
        end

        # Detect zero crossings in predictions (slack water for currents)
        def detect_zero_crossings(predictions)
            crossings = []
            return crossings if predictions.length < 2

            (1...predictions.length).each do |i|
                prev = predictions[i-1]
                curr = predictions[i]

                # Check for sign change (zero crossing)
                if (prev['height'] > 0 && curr['height'] <= 0) ||
                   (prev['height'] < 0 && curr['height'] >= 0)

                    # Linear interpolation to find approximate crossing time
                    if prev['height'] != curr['height']
                        ratio = prev['height'].abs / (prev['height'].abs + curr['height'].abs)
                        time_delta = curr['time'] - prev['time']
                        crossing_time = prev['time'] + (ratio * time_delta)
                    else
                        crossing_time = curr['time']
                    end

                    crossings << {
                        'time' => crossing_time,
                        'height' => 0.0,
                        'units' => curr['units'],
                        # Negative to positive is the slack before flood
                        'begins' => prev['height'] < 0 ? 'flood' : 'ebb'
                    }
                end
            end

            crossings
        end

        def tide_data_for(station, around)
            start_time = beginning_of_window(around)
            end_time = end_of_window(around)

            # Use bid if available (e.g. for currents with different depths), fallback to id
            lookup_id = station.bid || station.id

            # Use optimized coarse-to-fine peak generation (93% fewer prediction points).  A
            # current can have the same id (see Engine#cache_entry), so look among tides only.
            peaks = @engine.generate_peaks_optimized(lookup_id, start_time, end_time, type: 'tide')

            peaks.map do |p|
                Models::TideData.new(
                    type: p['type'],
                    units: p['units'],
                    prediction: p['height'],
                    time: p['time'].to_datetime,
                    url: "#xtide"
                )
            end
        end
    end
end

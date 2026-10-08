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

            # Use bid if available (e.g. for currents with different depths), fallback to id
            lookup_id = station.bid || station.id
            predictions = @engine.generate_predictions(lookup_id, start_time, end_time)

            return [] if predictions.empty?

            # Detect peaks (maxima and minima of the signed velocity)
            peaks = @engine.detect_peaks(predictions)

            # Slack water: the zero crossings of the velocity
            slacks = slack_waters(lookup_id, predictions, start_time, end_time)

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

            # Sort by time
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

        # Slack water times for a current station.  For a reference station they
        # are the zero crossings of its predictions.  A subordinate station's
        # predictions are only its max flood and ebb peaks, so its slacks are the
        # reference station's zero crossings moved by the subordinate's
        # flood-begins and ebb-begins time offsets.
        def slack_waters(lookup_id, predictions, start_time, end_time)
            station_data = @engine.stations_cache[lookup_id] || {}
            ref_key = station_data['ref_key']
            flood_begins = station_data['flood_begins']
            ebb_begins = station_data['ebb_begins']

            return detect_zero_crossings(predictions) unless ref_key && flood_begins && ebb_begins

            # The same 2 hour margin generate_predictions uses for subordinate stations
            ref_predictions = @engine.generate_predictions(ref_key, start_time - 2.hours, end_time + 2.hours)

            detect_zero_crossings(ref_predictions).filter_map do |c|
                offset = c['begins'] == 'flood' ? flood_begins : ebb_begins
                time = c['time'] + offset_to_seconds(offset)
                next unless time >= start_time && time <= end_time

                c.merge('time' => time)
            end
        end

        # "[+-]HH:MM:SS" -> seconds
        def offset_to_seconds(offset)
            sign = offset.start_with?('-') ? -1 : 1
            h, m, sec = offset.delete('+-').split(':').map(&:to_i)
            sign * (h * 3600 + m * 60 + (sec || 0))
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

            # Use optimized coarse-to-fine peak generation (93% fewer prediction points)
            peaks = @engine.generate_peaks_optimized(lookup_id, start_time, end_time)

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

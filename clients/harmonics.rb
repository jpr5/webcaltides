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
            # For now, XTide/TICON current data is predicted the same way as tide data
            # but represents velocity. generate_predictions returns height/velocity.
            tide_data_for(station, around).map do |d|
                Models::CurrentData.new(
                    type: d.type.downcase == 'high' ? 'flood' : 'ebb', # Mapping peak types to flood/ebb for now
                    time: d.time,
                    prediction: d.prediction,
                    velocity_major: d.prediction,
                    units: d.units,
                    depth: station.depth,
                    url: d.url
                )
            end
        end

        def tide_data_for(station, around)
            start_time = beginning_of_window(around)
            end_time = end_of_window(around)

            # Use bid if available (e.g. for currents with different depths), fallback to id
            lookup_id = station.bid || station.id
            predictions = @engine.generate_predictions(lookup_id, start_time, end_time)
            peaks = @engine.detect_peaks(predictions)

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

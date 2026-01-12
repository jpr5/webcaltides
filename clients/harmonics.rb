require_relative 'base'
require_relative '../models/station'
require_relative '../models/tide_data'
require_relative '../lib/harmonics_engine'

module Clients
    class Harmonics < Base
        include TimeWindow

        # Harmonics usually cover a full year or more, but we'll stick to the window pattern
        self.window_size = 13.months

        def initialize(logger)
            super(logger)
            @engine = ::Harmonics::Engine.new(logger, WebCalTides.settings.cache_dir)
        end

        def tide_stations
            return [] unless File.exist?(@engine.harmonics_file)

            @engine.stations.map { |s| Models::Station.from_hash(s.stringify_keys) }
        end

        def tide_data_for(station, around)
            start_time = beginning_of_window(around)
            end_time = end_of_window(around)

            predictions = @engine.generate_predictions(station.id, start_time, end_time)
            peaks = @engine.detect_peaks(predictions)

            peaks.map do |p|
                Models::TideData.new(
                    type: p['type'],
                    units: p['units'],
                    prediction: p['height'],
                    time: p['time'].to_datetime,
                    url: "#harmonics"
                )
            end
        end
    end
end

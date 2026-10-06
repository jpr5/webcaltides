module Models
    class TideData < Struct.new(:type, :units, :prediction, :time, :url, :dataset_year, :notes)

        # When modifying this class, bump this version
        def self.version
            2
        end

        def self.from_hash(h)
            TideData.new(
                type: h['type'],
                prediction: h['prediction'],
                time: DateTime.parse(h['time']),
                url: h['url'],
                units: h['units'],
                dataset_year: h['dataset_year'],
                notes: h['notes']
            )
        end
    end
end

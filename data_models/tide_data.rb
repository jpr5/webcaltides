module DataModels
    class TideData < Struct.new(:type, :units, :prediction, :time, :url)

        # When modifying this class, bump this version
        def self.version
            1
        end

        def self.from_hash(h)
            TideData.new(
                type: h['type'],
                prediction: h['prediction'],
                time: DateTime.parse(h['time']),
                url: h['url'],
                units: h['units']
            )
        end
    end
end
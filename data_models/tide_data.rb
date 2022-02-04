module DataModels
    class TideData

        # When modifying this class, bump this version
        def self.version
            1
        end

        attr_accessor :type
        attr_accessor :units
        attr_accessor :prediction
        attr_accessor :time
        attr_accessor :url
        
        def initialize(type:, units:, prediction:, time:, url:)
            @type = type
            @prediction = prediction
            @time = time
            @url = url
            @units = units
        end

        def to_hash
            {
                type: type,
                prediction: prediction,
                time: time,
                url: url,
                units: units
            }
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
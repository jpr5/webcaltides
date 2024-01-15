module DataModels
    class Station

        # When modifying this class, bump this version
        # v2: extended to support current stations, in addition to tide stations
        def self.version
            2
        end

        attr_accessor :name
        attr_accessor :alternate_names
        attr_accessor :id
        attr_accessor :public_id
        attr_accessor :region
        attr_accessor :location
        attr_accessor :lat
        attr_accessor :lon
        attr_accessor :url
        attr_accessor :provider
        attr_accessor :bid
        attr_accessor :depth

        def initialize(name:, alternate_names:nil, bid:nil, id:, public_id:nil, region:nil, location:nil, lat:, lon:, url:, provider:, depth:nil)
            @name = name
            @alternate_names = alternate_names
            @id = id
            @bid = bid
            @public_id = public_id
            @region = region
            @location = location
            @lat = lat
            @lon = lon
            @url = url
            @provider = provider
            @depth = depth
        end

        def to_hash
            {
                name: name,
                alternate_names: alternate_names,
                id: id,
                public_id: public_id,
                region: region,
                location: location,
                lat: lat,
                lon: lon,
                url: url,
                provider: provider,
                bid: bid,
                depth: depth,
            }
        end

        def self.from_hash(h)
            Station.new(
                name: h['name'],
                alternate_names: h['alternate_names'],
                id: h['id'],
                public_id: h['public_id'],
                region: h['region'],
                location: h['location'],
                lat: h['lat'],
                lon: h['lon'],
                url: h['url'],
                provider: h['provider'],
                bid: h['bid'],
                depth: h['depth'],
            )
        end
    end
end
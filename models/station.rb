module Models
    class Station < Struct.new(:name, :alternate_names, :id, :public_id, :region, :location,
                               :lat, :lon, :url, :provider, :bid, :depth)

        # When modifying this class, bump this version
        # v2: extended to support current stations, in addition to tide stations
        def self.version
            2
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

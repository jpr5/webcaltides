module DataModels
    class CurrentData < Struct.new(:bin, :type, :mean_flood_dir, :mean_ebb_dir, :time, :depth, :velocity_major, :url)

        def self.version
            1
        end

        def self.from_hash(h)
            CurrentData.new(    # API || cached/internal
                bin:            h['Bin'] || h['bin'],
                type:           h['Type'] || h['type'],
                time:           h['Time'] || h['time'],
                depth:          h['Depth'] || h['depth'],
                mean_ebb_dir:   h['meanEbbDir'] || h['mean_ebb_dir'],
                mean_flood_dir: h['meanFloodDir'] || h['mean_flood_dir'],
                velocity_major: h['Velocity_Major'] || h['velocity_major'],
                url:            h['Url'] || h['url'],
            ).tap do |cd|
                cd.time = DateTime.parse(cd.time) if cd.time.kind_of? String
            end
        end

    end
end


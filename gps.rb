
module WebCalTides
module GPS

    extend self

    def normalize(coord_string)
        # Handle the case where coord_string is already a decimal pair like "-33.8688, 151.2093"
        if coord_string.match(/^\s*-?\d+\.\d+\s*,\s*-?\d+\.\d+\s*$/)
            parts = coord_string.split(',').map(&:strip)
            latitude = parts[0].to_f
            longitude = parts[1].to_f

            # Return normalized formats
            return {
                decimal: "#{latitude}, #{longitude}",
                dms: format_as_dms(latitude, longitude),
                latitude: latitude,
                longitude: longitude
            }
        end

        parts = split_lat_lon(coord_string)

        lat_part = nil
        lon_part = nil

        # First pass: try to identify parts by hemisphere indicators
        parts.each do |part|
            if part.match(/[ns]/i)
                lat_part = part
            elsif part.match(/[ew]/i)
                lon_part = part
            end
        end

        # If we couldn't identify by hemisphere, try to parse both parts
        if lat_part.nil? || lon_part.nil?
            # Handle space-separated coordinates within parts
            all_coords = []
            parts.each do |part|
                # Split on spaces and filter out empty strings
                space_split = part.split(/\s+/).reject(&:empty?)
                all_coords.concat(space_split)
            end

            # Try to identify lat/lon from all coordinate pieces
            all_coords.each do |coord|
                if coord.match(/[ns]/i) && lat_part.nil?
                    lat_part = coord
                elsif coord.match(/[ew]/i) && lon_part.nil?
                    lon_part = coord
                end
            end

            # If still no hemisphere indicators, assume first is lat, second is lon
            if (lat_part.nil? || lon_part.nil?) && parts.length >= 2
                # For decimal format like "-33.8688, 151.2093"
                lat_part = parts[0] if lat_part.nil?
                lon_part = parts[1] if lon_part.nil?
            end
        end

        lat = parse_single_coordinate(lat_part, :lat)
        lon = parse_single_coordinate(lon_part, :lon)

        raise "could not parse coordinates from: #{coord_string}" if lat.nil? || lon.nil?

        return {
            decimal: "#{lat}, #{lon}",
            dms: format_as_dms(lat, lon),
            latitude: lat,
            longitude: lon
        }
    end

    private

    def split_lat_lon(input)
        if input.include?(',')
            parts = input.split(',').map(&:strip)
        else
            # Try to match two DMS+hemisphere blocks in a row (with or without whitespace between)
            match = input.match(/(.+?[NS])\s*(.+?[EW])/i)
            if match
                parts = [match[1].strip, match[2].strip]
            else
                # fallback: scan for DMS+hemisphere blocks
                blocks = input.scan(/(\d{1,3}[^NSEW\d]*\d*\.?\d*[^NSEW\d]*[NSWE])/i).flatten

                if blocks.size == 2
                    parts = blocks.map(&:strip)
                else
                    mid = input.size / 2
                    parts = [input[0...mid].strip, input[mid..-1].strip]
                end
            end
        end

        parts = parts.map { |p| p.nil? ? '' : p.strip }

        raise "could not split lat/lon from: #{input} -> #{parts.inspect}" if parts.nil? || parts.size != 2 || parts.any?(&:empty?)

        return parts
    end

    # Parse a single coordinate (lat or lon)
    def parse_single_coordinate(str, which)
        if str.nil? || str.strip.empty?
            warn "parse_single_coordinate called with nil or empty string for #{which}"
            return nil
        end
        s = str.strip.upcase
        hemisphere = nil
        s.gsub!(/([NSEW])/) do |h|
            hemisphere = h
            ''
        end
        s.gsub!(/[\"]/, '') # Remove quotes
        s.gsub!(/[°′'″]/, ' ') # Replace all DMS symbols with space
        s = s.gsub(/[NSEW]/, '').strip # Remove hemisphere again just in case
        nums = s.split(/\s+/).map(&:to_f)

        decimal = nil
        if nums.size == 3
            decimal = nums[0].abs + nums[1]/60.0 + nums[2]/3600.0
        elsif nums.size == 2
            decimal = nums[0].abs + nums[1]/60.0
        elsif nums.size == 1
            decimal = nums[0]
        else
            warn "parse_single_coordinate could not extract numbers from '#{str}' for #{which}"
            return nil
        end
        # Determine sign
        if hemisphere
            if (which == :lat && hemisphere == 'S') || (which == :lon && hemisphere == 'W')
                decimal = -decimal.abs
            else
                decimal = decimal.abs
            end
        elsif which == :lon && nums[0] < 0
            # If longitude and first number is negative, force negative
            decimal = -decimal.abs
        else
            # fallback: for longitude, treat values > 180 as negative (rare, but for 0-360)
            decimal = -decimal if which == :lon && decimal > 180
        end
        return decimal
    end

    def format_as_dms(lat, lon)
        lat_dms = decimal_to_dms(lat.abs)
        lon_dms = decimal_to_dms(lon.abs)
        lat_hem = lat >= 0 ? 'N' : 'S'
        lon_hem = lon >= 0 ? 'E' : 'W'
        return "#{lat_dms}#{lat_hem}, #{lon_dms}#{lon_hem}"
    end

    def decimal_to_dms(decimal)
        degrees = decimal.floor
        minutes_float = (decimal - degrees) * 60
        minutes = minutes_float.floor
        seconds = (minutes_float - minutes) * 60
        return "#{degrees}°#{minutes}'#{seconds.round(1)}\""
    end
end
end

# Testing
if __FILE__ == $0
    TEST_COORDS = [
        # Decimal degrees
        ["40.7128° N, 74.0060° W", [40.7128, -74.006]],
        ["-33.8688, 151.2093", [-33.8688, 151.2093]],
        ["37.7749 N, 122.4194 W", [37.7749, -122.4194]],
        ["51.0, -0.0", [51.0, -0.0]],
        # DMS
        ["51°30'26\"N, 0°7'39\"W", [51.507222, -0.1275]],
        ["48°51'29.5\"N, 2°17'40.2\"E", [48.858194, 2.2945]],
        ["33°52'7.7\"S, 151°12'33.5\"E", [-33.868806, 151.209306]],
        # DM
        ["51°30.5'N, 0°7.65'W", [51.508333, -0.1275]],
        # Whitespace and hemisphere
        ["40 42 46.1 N, 74 0 21.6 W", [40.712806, -74.006]],
        ["40 42 46.1, -74 0 21.6", [40.712806, -74.006]],
        ["40°42'46.1\" N 74°0'21.6\" W", [40.712806, -74.006]],
        # Edge cases
        ["0°0'0\"N, 0°0'0\"E", [0.0, 0.0]],
        ["90°0'0\"S, 180°0'0\"W", [-90.0, -180.0]],
    ]

    TEST_COORDS.each do |input, expected|
        puts "\nInput: #{input}"
        begin
            result = WebCalTides::GPS.normalize(input)
            puts "Decimal Degrees: #{result[:decimal]}"
            puts "DMS Format: #{result[:dms]}"
            puts "Latitude: #{result[:latitude]}"
            puts "Longitude: #{result[:longitude]}"
            # Check if close to expected
            lat_ok = (result[:latitude] - expected[0]).abs < 0.0002
            lon_ok = (result[:longitude] - expected[1]).abs < 0.0002
            if lat_ok && lon_ok
                puts "PASS"
            else
                puts "FAIL: Expected #{expected.inspect}"
            end
        rescue => e
            puts "ERROR: #{e}"
        end
    end
end

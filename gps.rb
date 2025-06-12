require 'geo_ruby/simple_features' if __FILE__ == $0 # to test: ruby gps.rb

module WebCalTides
module GPS

    extend self

    include GeoRuby::SimpleFeatures

    def normalize(coord_string)
        parts = coord_string.split(',').map(&:strip)

        lat_part = nil
        lon_part = nil

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
            if lat_part.nil? && lon_part.nil? && all_coords.length >= 2
                lat_part = all_coords[0]
                lon_part = all_coords[1]
            end
        end

        latitude = parse_coordinate(lat_part)
        longitude = parse_coordinate(lon_part)

        # Validate that we got valid coordinates
        if latitude.nil? || longitude.nil?
            $LOG.error "could not parse coordinates from: #{coord_string}"
            raise
        end

        # Create a Point object
        point = Point.from_x_y(longitude, latitude)

        # Return normalized formats
        {
            decimal: "#{latitude}, #{longitude}",
            dms: format_as_dms(latitude, longitude),
            point: point,
            latitude: latitude,
            longitude: longitude
        }
    end

    private

    def parse_coordinate(coord_str)
        return nil if coord_str.nil? || coord_str.empty?

        # Remove extra whitespace and quotes
        coord = coord_str.strip.gsub(/["']/, '')

        # Check for hemisphere indicators or explicit negative sign
        is_negative = coord.match(/[sw]/i) || coord.start_with?('-')

        # Remove hemisphere indicators for parsing but preserve negative sign
        coord = coord.gsub(/[nsew]/i, '').strip

        # Try to match degrees, minutes, seconds format: 144°27'38.5
        if coord.match(/(\d+(?:\.\d+)?)°(\d+(?:\.\d+)?)'([\d.]+)/)
            degrees = $1.to_f
            minutes = $2.to_f
            seconds = $3.to_f

            decimal = degrees + (minutes / 60.0) + (seconds / 3600.0)
        elsif coord.match(/(\d+(?:\.\d+)?)°(\d+(?:\.\d+)?)'/)
            # Degrees and decimal minutes: 144°27'
            degrees = $1.to_f
            minutes = $2.to_f

            decimal = degrees + (minutes / 60.0)
        elsif coord.match(/(\d+(?:\.\d+)?)°/)
            # Just degrees: 144°
            degrees = $1.to_f
            decimal = degrees
        elsif coord.match(/^(\d+(?:\.\d+)?)$/)
            # Just a number (assume decimal degrees)
            decimal = $1.to_f
        else
            # Try to extract any number from the string
            numbers = coord.scan(/\d+(?:\.\d+)?/).map(&:to_f)
            return nil if numbers.empty?

            # If multiple numbers, assume DMS format
            if numbers.length >= 3
                decimal = numbers[0] + (numbers[1] / 60.0) + (numbers[2] / 3600.0)
            elsif numbers.length == 2
                decimal = numbers[0] + (numbers[1] / 60.0)
            else
                decimal = numbers[0]
            end
        end

        # Apply hemisphere (negative for South/West)
        decimal = -decimal if is_negative

        decimal
    end

    def format_as_dms(lat, lon)
        lat_dms = decimal_to_dms(lat.abs)
        lon_dms = decimal_to_dms(lon.abs)

        lat_hemisphere = lat >= 0 ? 'N' : 'S'
        lon_hemisphere = lon >= 0 ? 'E' : 'W'

        "#{lat_dms}#{lat_hemisphere}, #{lon_dms}#{lon_hemisphere}"
    end

    def decimal_to_dms(decimal)
        degrees = decimal.floor
        minutes_float = (decimal - degrees) * 60
        minutes = minutes_float.floor
        seconds = (minutes_float - minutes) * 60

        "#{degrees}°#{minutes}'#{seconds.round(1)}\""
    end
end
end

# Testing
if __FILE__ == $0
    [
        "40.7128° N, 74.0060° W",
        "51°30'26\"N 0°7'39\"W",
        "-33.8688, 151.2093",
        "48°51'29.5\"N 2°17'40.2\"E",
    ].each do |coords|

        puts "\nInput: #{coords}"

        result = WebCalTides::GPS.normalize(coords)

        puts "Normalized Results:"
        puts "Decimal Degrees: #{result[:decimal]}"
        puts "DMS Format: #{result[:dms]}"
        puts "Latitude: #{result[:latitude]}"
        puts "Longitude: #{result[:longitude]}"

    end
end

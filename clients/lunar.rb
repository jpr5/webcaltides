require_relative 'base'
require 'date'
require 'json'

module Clients
    class Lunar < Base
        USNO_API_URL = 'https://aa.usno.navy.mil/api/moon/phases/year'
        ASTRONOMICS_API_URL = 'https://www.astronomics.com/api/v1/moon/phases'

        def phase(date)
            angle = phase_angle(date)

            case
            when (angle >= 0 && angle < 22.5) || (angle >= 337.5 && angle < 360)
                :new
            when angle >= 22.5 && angle < 67.5
                :waxing_crescent
            when angle >= 67.5 && angle < 112.5
                :first_quarter
            when angle >= 112.5 && angle < 157.5
                :waxing_gibbous
            when angle >= 157.5 && angle < 202.5
                :full
            when angle >= 202.5 && angle < 247.5
                :waning_gibbous
            when angle >= 247.5 && angle < 292.5
                :last_quarter
            else
                :waning_crescent
            end
        end

        def phase_angle(date)
            jd = date.ajd + 0.5

            # Time in Julian centuries since J2000.0
            t = (jd - 2451545.0) / 36525.0

            # Sun's mean longitude
            l0 = 280.46646 + 36000.76983 * t + 0.0003032 * t * t
            l0 = normalize_angle(l0)

            # Sun's mean anomaly
            m0 = 357.52911 + 35999.05029 * t - 0.0001537 * t * t
            m0 = normalize_angle(m0)

            # Moon's mean longitude
            l = 218.3165 + 481267.8813 * t
            l = normalize_angle(l)

            # Moon's mean anomaly
            m = 134.9634 + 477198.8675 * t + 0.0087414 * t * t
            m = normalize_angle(m)

            # Moon's argument of latitude
            f = 93.2721 + 483202.0175 * t - 0.0036539 * t * t
            f = normalize_angle(f)

            # Moon's mean elongation from the Sun
            d = 297.8502 + 445267.1115 * t - 0.0016300 * t * t
            d = normalize_angle(d)

            # Calculate the phase angle (difference between geocentric longitudes of the Moon and Sun)
            phase_angle = l - l0

            # Apply corrections for more accuracy
            corrections = 0.0

            # Major periodic terms
            corrections += -6.289 * Math.sin(degrees_to_radians(m))
            corrections += 2.100 * Math.sin(degrees_to_radians(m0))
            corrections += -1.274 * Math.sin(degrees_to_radians(2*d - m))
            corrections += -0.658 * Math.sin(degrees_to_radians(2*d))
            corrections += -0.214 * Math.sin(degrees_to_radians(2*m))

            # Apply corrections
            phase_angle += corrections

            # Normalize to 0-360 degrees
            normalize_angle(phase_angle)
        end

        def percent_full(date)
            angle = phase_angle(date)
            (1 - Math.cos(degrees_to_radians(angle))) / 2
        end

        def phases_for_year(year)
            # Try USNO API first
            begin
                phases = fetch_from_usno_api(year.to_i)
                return phases if phases && !phases.empty?
            rescue => e
                logger.error "error fetching from USNO API: #{e.message}"
            end

            # If USNO fails, try Astronomics API
            begin
                phases = fetch_from_astronomics_api(year.to_i)
                return phases if phases && !phases.empty?
            rescue => e
                logger.error "error fetching from Astronomics API: #{e.message}"
            end

            # If all APIs fail, log a warning and return nil
            logger.warn "no lunar phase data available for #{year}"
            return nil
        end

        private

        def degrees_to_radians(degrees)
            degrees * Math::PI / 180
        end

        def normalize_angle(angle)
            angle = angle % 360
            angle < 0 ? angle + 360 : angle
        end

        def fetch_from_usno_api(year)
            logger.debug "connecting to USNO API for #{year}"

            params = {
                'year': year,
                'month': 1,  # Start from January
                'day': 1,
                'nump': 40   # Get all phases for the year
            }

            # Build the URL with query parameters
            query_string = params.map { |k, v| "#{k}=#{v}" }.join('&')
            full_url = "#{USNO_API_URL}?#{query_string}"

            # Make the request
            logger.debug "requesting lunar phases from #{full_url}"

            begin
                # Use the base class's get_url method
                json = get_url(full_url)

                # Parse the JSON response
                data = JSON.parse(json)
                return process_usno_data(data)

            rescue => e
                logger.error "error fetching from USNO API: #{e.message}"
                raise e
            end

            return nil
        end

        def fetch_from_astronomics_api(year)
            logger.debug "connecting to Astronomics API for #{year}"

            params = {
                'year': year,
                'format': 'json'
            }

            query_string = params.map { |k, v| "#{k}=#{v}" }.join('&')
            full_url = "#{ASTRONOMICS_API_URL}?#{query_string}"

            logger.debug "requesting lunar phases from Astronomics API: #{full_url}"

            begin
                json = get_url(full_url)

                data = JSON.parse(json)
                return process_astronomics_data(data)

            rescue => e
                logger.error "error fetching from Astronomics API: #{e.message}"
                raise e
            end

            return nil
        end

        def process_usno_data(data)
            logger.debug "usno: processing API data for lunar phases"

            phases = []

            if !data || !data['phasedata']
                logger.error "usno: invalid response format"
                logger.debug "usno: response: #{data.inspect}"
                return nil
            end

            data = data['phasedata']

            #logger.debug "usno: raw API data: #{data.inspect}"

            data.each do |entry|
                begin
                    pt = entry['phase']&.downcase
                    dt = DateTime.parse("#{entry['year']}/#{entry['month']}/#{entry['day']} #{entry['time']}")

                    next unless pt && dt
                    next unless st = case pt
                        when 'new moon'      then :new_moon
                        when 'first quarter' then :first_quarter
                        when 'full moon'     then :full_moon
                        when 'last quarter'  then :last_quarter
                        else nil
                    end

                    #logger.debug "usno: adding #{st} at #{dt.asctime}"

                    phases << {
                        datetime: dt,
                        type: st,
                    }
                rescue => e
                    logger.error "usno: failed to process entry: #{e.message}"
                    next
                end
            end

            if !phases.empty?
                phases.sort_by! { |phase| phase[:datetime] }

                logger.debug "usno: extracted #{phases.length} lunar phases"
                return phases
            end

            logger.warn "usno: no lunar phases found"
            return nil
        end

        def process_astronomics_data(data)
            logger.debug "astron: processing API data for lunar phases"

            phases = []

            if !data || !data['moonPhases'] || !data['moonPhases'].is_a?(Array)
                logger.error "astron: invalid response format"
                return nil
            end

            data = data['moonPhases']

            #logger.debug "astron: raw API data: #{data.inspect}"

            data.each do |entry|
                begin
                    pt = entry['phase']&.downcase
                    dt = DateTime.parse("#{entry['date']} #{entry['time']}")

                    next unless pt && dt
                    next unless st = case pt
                        when 'new', 'new moon'   then :new_moon
                        when 'full', 'full moon' then :full_moon
                        when 'first quarter',
                             'first'             then :first_quarter
                        when 'last quarter',
                             'third quarter',
                             'last', 'third'     then :last_quarter
                        else nil
                    end

                    #logger.debug "astron: adding #{st} at #{dt.asctime}"

                    phases << {
                        datetime: dt,
                        type: st,
                    }
                rescue => e
                    logger.error "astron: failed to process entry: #{e.message}"
                    next
                end
            end

            if !phases.empty?
                phases.sort_by! { |phase| phase[:datetime] }

                logger.debug "astron: extracted #{phases.length} lunar phases"
                return phases
            end

            logger.warn "astron: no lunar phases found"
            return nil
        end

    end
end

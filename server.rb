##
## Copyright (C) 2025 Jordan Ritter <jpr5@darkridge.com>
##
## WebCal server for Tides, Currents, Sunset and Lunar information.  Meant to
## replace sailwx.info/tides.mobilegeographics.com, which as of 2021 appears no
## longer to work.
##

require 'logger'
$LOG = Logger.new(STDOUT).tap do |log|
    log.formatter = proc { |s, d, _, m| "#{d.strftime("%Y-%m-%d %H:%M:%S")} #{s} #{m}\n" }
end

require_relative 'webcaltides'

class Server < ::Sinatra::Base

    set :app_file,      File.expand_path(__FILE__)
    set :root,          File.expand_path(File.dirname(__FILE__))
    set :cache_dir,     settings.root + '/cache'
    set :public_folder, settings.root + '/public'
    set :views,         settings.root + '/views'
    set :static,        true

    configure do
        enable :show_exceptions
        disable :sessions, :logging

        helpers ::Rack::Utils

        Timezone::Lookup.config(:geonames) do |c|
            c.username = ENV['USER']
        end

        FileUtils.mkdir_p settings.cache_dir
    end

    configure :development do
        set :logging, $LOG
        register Sinatra::Reloader
        also_reload File.expand_path("./webcaltides.rb")
        also_reload File.expand_path("./gps.rb")
        after_reload { $LOG.debug 'reloaded' }
    end

    ##
    ## URL entry points
    ##

    get "/" do
        erb :index, locals: { searchtext: nil, units: nil }
    end

    # Autocomplete endpoint for station search
    get "/api/stations/autocomplete" do
        query = (params['q'] || '').strip.downcase
        return { results: [] }.to_json if query.length < 2

        # Search both tide and current stations
        all_stations = WebCalTides.tide_stations + WebCalTides.current_stations

        # Filter and dedupe by name
        matches = all_stations
            .select { |s| s.name.downcase.include?(query) || s.region&.downcase&.include?(query) }
            .uniq { |s| [s.name, s.region] }
            .first(10)
            .map { |s| { name: s.name, region: s.region, type: s.depth ? 'current' : 'tide' } }

        content_type :json
        { results: matches }.to_json
    end

    # API endpoint to compare predictions between multiple stations
    # GET /api/stations/compare?type=tides&ids[]=noaa:123&ids[]=xtide:456
    # GET /api/stations/compare?type=currents&ids[]=noaa:123&ids[]=xtide:456
    get "/api/stations/compare" do
        content_type :json

        station_type = params['type'] || 'tides'
        return { error: 'Invalid type' }.to_json unless station_type.in?(%w[tides currents])

        station_ids = Array(params['ids'])
        return { error: 'No station IDs provided' }.to_json if station_ids.empty?
        return { error: 'Maximum 5 stations allowed' }.to_json if station_ids.length > 5

        results = station_ids.map do |id|
            station = case station_type
                      when 'tides'    then WebCalTides.tide_station_for(id)
                      when 'currents' then WebCalTides.current_station_for(id)
                      end
            next nil unless station

            events = case station_type
                     when 'tides'    then WebCalTides.next_tide_events(id)
                     when 'currents' then WebCalTides.next_current_events(id)
                     end

            result = {
                id: id,
                name: station.name,
                provider: station.provider,
                events: (events || []).map { |e| e.merge(time: e[:time].iso8601) }
            }
            result[:depth] = station.depth if station.respond_to?(:depth) && station.depth
            result
        end.compact

        return { error: 'No valid stations found' }.to_json if results.empty?

        # Compute per-event deltas relative to first station (primary)
        # Match events by type (High/Low for tides, Flood/Ebb/Slack for currents)
        primary = results.first
        primary_events = primary[:events]

        results[1..].each do |alt|
            alt_events = alt[:events]
            event_deltas = []

            # For each primary event, find matching type in alternative and compute delta
            primary_events.each_with_index do |p_event, idx|
                # Find matching event type in alternative (same index or same type)
                a_event = alt_events[idx] if alt_events[idx] && alt_events[idx][:type] == p_event[:type]
                a_event ||= alt_events.find { |e| e[:type] == p_event[:type] }

                if a_event
                    time_diff = (Time.parse(a_event[:time]) - Time.parse(p_event[:time])).to_i

                    if station_type == 'currents'
                        # Skip velocity comparison for Slack events (no velocity)
                        if p_event[:type] != 'Slack' && p_event[:velocity] && a_event[:velocity]
                            velocity_diff = (a_event[:velocity].to_f - p_event[:velocity].to_f).round(2)
                            units = p_event[:velocity_units] || 'kn'
                            event_deltas << {
                                type: p_event[:type],
                                time: WebCalTides.format_time_delta(time_diff),
                                value: WebCalTides.format_height_delta(velocity_diff, units)
                            }
                        else
                            event_deltas << {
                                type: p_event[:type],
                                time: WebCalTides.format_time_delta(time_diff),
                                value: nil
                            }
                        end
                    else
                        height_diff = (a_event[:height].to_f - p_event[:height].to_f).round(2)
                        units = p_event[:units] || 'ft'
                        event_deltas << {
                            type: p_event[:type],
                            time: WebCalTides.format_time_delta(time_diff),
                            value: WebCalTides.format_height_delta(height_diff, units)
                        }
                    end
                end
            end

            alt[:event_deltas] = event_deltas

            # Also compute a summary delta from first comparable events (for dropdown display)
            first_delta = event_deltas.find { |d| d[:value] } || event_deltas.first
            if first_delta
                alt[:delta] = {
                    time: first_delta[:time],
                    height: station_type == 'tides' ? first_delta[:value] : nil,
                    velocity: station_type == 'currents' ? first_delta[:value] : nil
                }
            end
        end

        { stations: results }.to_json
    end

    # API endpoint to get next tide/current event for a station
    get "/api/stations/:type/:id/next" do
        content_type :json

        events = case params[:type]
                 when 'tides'    then WebCalTides.next_tide_events(params[:id])
                 when 'currents' then WebCalTides.next_current_events(params[:id])
                 end

        if events.nil?
            $LOG.warn "No station data for #{params[:type]}/#{params[:id]}"
            return { error: 'Station not found' }.to_json
        end

        if events.empty?
            $LOG.debug "No future events for #{params[:type]}/#{params[:id]}"
        end

        # Return ISO 8601 timestamps - client will format based on user's timezone preference
        formatted = events.map do |e|
            e.merge(time: e[:time].iso8601)
        end

        { events: formatted }.to_json
    end

    post "/" do
        radius       = params['within'].to_i
        radius_units = params['units'] == 'metric' ? 'km' : 'mi'
        searchparam  = (params['searchtext'] || '').strip
        searchtext   = searchparam.dup.downcase.tr('“”', '""')
        # ^^^ Depending on your font these quotes may look the same -- but they're not

        return erb :index, locals: { searchtext: nil, units: nil } unless searchtext.length > 0

        # If we see anything like "42.1234, 1234.0132" then treat it like a GPS search
        if searchtext.match(/\d[°'.]\s*\d/)
            how = "near"

            unless tokens = WebCalTides.parse_gps(searchtext)
                $LOG.warn "unable to parse '#{searchtext}' as GPS"
                return erb :index, locals: { searchtext: nil, units: nil }
            end

            (lat, long) = *tokens
            radius      = 10 # default

            tide_results    = WebCalTides.find_tide_stations_by_gps(lat, long, within:radius, units: radius_units)
            current_results = WebCalTides.find_current_stations_by_gps(lat, long, within:radius, units: radius_units)
        else
            how = "by"

            # Parse search terms.  Matched quotes are taken as-is (still
            # lowercased), while everything else is tokenized via [ ,]+.
            tokens = searchtext.scan(/["]([^"]+)["]/).flatten
            searchtext.gsub!(/["]([^"]+)["]/, '')
            tokens += searchtext.split(/[, ]+/).reject(&:empty?)

            unless tokens.empty?
                tide_results    = WebCalTides.find_tide_stations(by:tokens, within:radius, units: radius_units)
                current_results = WebCalTides.find_current_stations(by:tokens, within:radius, units: radius_units)
            end
        end

        tide_results    ||= []
        current_results ||= []

        # Group stations by proximity to deduplicate results from different providers
        # Current stations also require matching depth to be grouped together
        tide_groups = WebCalTides.group_search_results(tide_results, compute_deltas: false)
        current_groups = WebCalTides.group_search_results(current_results, compute_deltas: false, match_depth: true)

        for_what  = "#{tokens}"
        for_what += " within #{radius}#{radius_units}" if radius

        $LOG.info "search #{how} #{for_what} yields #{tide_groups.count + current_groups.count} grouped results (from #{tide_results.count + current_results.count} raw)"

        erb :index, locals: { tide_results: tide_groups, current_results: current_groups,
                              tokens: tokens, how:how, radius: radius, units: ERB::Util.html_escape_once(params['units'] || 'imperial'),
                              placeholder: ERB::Util.html_escape_once(searchparam.empty? ? 'Station...' : searchparam)
                            }
    end

    # For currents, station can be either an ID (we'll use the first bin) or a BID (specific bin)
    get "/:type/:station.ics" do
        type       = params[:type].tap { |type| type.in?(%w[tides currents]) or halt 404 }
        id         = params[:station].tap { |station| station.in?(WebCalTides.station_ids) or halt 404 }
        date       = Date.parse(params[:date]) rescue Time.current.utc # e.g. 20231201, for utility but unsupported in UI
        units      = params.fetch(:units, 'imperial').tap { |units| units.in?(%w[imperial metric]) or halt 422 }
        no_solar   = params[:solar].in?(%w[0 false]) # on by default
        add_lunar  = params[:lunar].in?(%w[1 true])  # off by default
        stamp      = date.utc.strftime("%Y%m")
        version    = type == "currents" ? Models::CurrentData.version : Models::TideData.version
        cached_ics = "#{settings.cache_dir}/#{type}_v#{version}_#{id}_#{stamp}_#{units}_#{no_solar ?"0":"1"}_#{add_lunar ?"1":"0"}.ics"

        # NOTE: Changed my mind on retval's.  In the shit-fucked-up case, we end up sending out a
        # full stack trace + 500, so really if we muck something up internally we should let the
        # exception float up.  Then we can assume that if we arrive without a calendar, then it's
        # simply not there -> 404.

        ics = File.read cached_ics rescue begin
            calendar = case type
                       when "tides"    then WebCalTides.tide_calendar_for(id, around: date, units: units) or halt 404
                       when "currents" then WebCalTides.current_calendar_for(id, around: date)            or halt 404
                       else halt 404
                       end

            # Add solar events if requested
            WebCalTides.solar_calendar_for(calendar, around:date) unless no_solar

            # Add lunar phase events if requested
            WebCalTides.lunar_calendar_for(calendar, around:date) if add_lunar

            calendar.publish

            $LOG.debug "caching to #{cached_ics}"
            File.write cached_ics, ical = calendar.to_ical

            ical
        end

        content_type 'text/calendar', charset: 'utf-8'
        body ics
    end

end

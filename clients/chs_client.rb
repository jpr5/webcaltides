require_relative '../data_models/station'
require_relative '../data_models/tide_data'

module Clients
    class ChsClient
        API_URL = 'https://api-iwls.dfo-mpo.gc.ca/api/v1'
        attr :logger

        def initialize(logger)
            @logger = logger
        end

        def tide_stations
            url = "#{API_URL}/stations"
            agent = Mechanize.new
            logger.info "getting tide station list from #{url}"
            json = agent.get(url).body
            logger.debug "json.length = #{json.length}"


            logger.debug "parsing tide station list"
            data = JSON.parse(json) rescue []

            stations = data.map do |js|
                DataModels::Station.new(
                    name: js['officialName'],
                    alternate_names: [],
                    id: js['id'],
                    public_id: js['code'],
                    region: region_for(js['latitude'], js['longitude']),
                    location: [js["officialName"], "Canada"].join(", "),
                    lat: js['latitude'],
                    lon: js['longitude'],
                    url: "https://www.tides.gc.ca/en/stations/#{js['code']}",
                    provider: 'chs'
                )
            end
            return stations
        end

        def region_for(lat, long)
            if long < -75 && long > -96 && lat < 64 && lat > 51
                'Hudson\'s Bay, Canada'
            elsif lat > 60
                'Northern Canada'
            elsif long < -120
                'Pacific Canada'
            elsif long > -75
                'Atlantic Canada'
            else
                'Canada'
            end
        end

        # around: base date, with a +/- N month window.  More processing per year, but this at least
        # solves the EOY data starvation problem, data is cached to disk and the 12x processing
        # (monthly vs. yearly) is cheap. 🤷‍♂️
        def tide_data_for(station, around, public_id)
            agent = Mechanize.new
            from = (around.utc.beginning_of_month - WebCalTides::WINDOW_SIZE).iso8601
            to   = (around.utc.end_of_month + WebCalTides::WINDOW_SIZE).iso8601
            url = "#{API_URL}/stations/#{station}/data?time-series-code=wlp-hilo&from=#{from}&to=#{to}"

            logger.info "getting json from #{url}"
            json = agent.get(url).body
            logger.debug "json.length = #{json.length}"

            logger.debug "parsing tide station list"
            data = JSON.parse(json) rescue []
            prev_value = data[1]['value']

            data.map do |jt|
                time = DateTime.parse(jt["eventDate"])
                td = DataModels::TideData.new(
                    type: jt['value'] >= prev_value ? "High" : "Low",
                    units: "m",
                    prediction: jt["value"],
                    time: time,
                    url: "https://www.tides.gc.ca/en/stations/#{public_id}/#{time.strftime("%Y-%m-%d")}"
                )
                prev_value = td.prediction
                td
            end
        end
    end
end
require './data_models/station'
require './data_models/tide_data'
require 'pry'
module Clients
    class NoaaClient
        API_URL = 'https://api.tidesandcurrents.noaa.gov'
        attr :logger

        def initialize(logger)
            @logger = logger
        end

        def tide_stations
            url = "#{API_URL}/mdapi/prod/webapi/tidepredstations.json?q="
            agent = Mechanize.new
            logger.info "getting tide station list from #{url}"
            json = agent.get(url).body
            logger.debug "json.length = #{json.length}"


            logger.debug "parsing tide station list"
            data = JSON.parse(json)["stationList"] rescue []

            stations = data.map do |js|
                DataModels::Station.new(
                    name: js['name'],
                    alternate_names: [js['etidesStnName'], js['commonName'], js['stationFullName']],
                    id: js['stationId'],
                    public_id: js['stationId'],
                    region: js['region'],
                    location: [js["etidesStnName"], js["region"], js["state"]].join(", "),
                    lat: js['lat'],
                    lon: js['lon'],
                    url: "https://tidesandcurrents.noaa.gov/stationhome.html?id=#{js['stationId']}",
                    provider: 'noaa'
                )
            end
            return stations
        end

        def tide_data_for(station, year, public_id)
            agent = Mechanize.new
            url = "#{API_URL}/api/prod/datagetter?product=predictions&datum=MLLW&time_zone=gmt&interval=hilo&units=english&application=web_services&format=json&begin_date=#{year}0101&end_date=#{year}1231&station=#{station}"

            logger.info "getting json from #{url}"
            json = agent.get(url).body
            logger.debug "json.length = #{json.length}"


            logger.debug "parsing tide station list"
            data = JSON.parse(json)["predictions"] rescue []

            data.map do |jt|
                time = DateTime.parse(jt["t"])
                DataModels::TideData.new(
                    type: jt["type"] == "H" ? "High" : "Low",
                    units: "ft",
                    prediction: jt["v"].to_f,
                    time: time,
                    url: "https://tidesandcurrents.noaa.gov/noaatidepredictions.html?id=#{public_id}&bdate=#{time.strftime("%Y%m%d")}"
                )
            end
        end
    end
end
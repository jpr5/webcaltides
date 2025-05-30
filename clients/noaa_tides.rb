require_relative 'base'
require_relative '../models/station'
require_relative '../models/tide_data'

module Clients
    class NoaaTides < Base

        API_URL            = 'https://api.tidesandcurrents.noaa.gov'
        PUBLIC_STATION_URL = "https://tidesandcurrents.noaa.gov/stationhome.html?id=%s"

        include TimeWindow

        # Get a full year (1 month behind + now + 11 ahead)
        self.window_size = 13.months

        def tide_stations
            url = "#{API_URL}/mdapi/prod/webapi/tidepredstations.json?q="

            logger.info "getting tide station list from #{url}"

            return nil unless json = get_url(url)

            logger.debug "parsing tide station list from API #{API_URL}"
            data = JSON.parse(json)["stationList"] rescue []

            stations = data.map do |js|
                Models::Station.new(
                    name: js['name'],
                    alternate_names: [js['etidesStnName'], js['commonName'], js['stationFullName']],
                    id: js['stationId'],
                    public_id: js['stationId'],
                    region: js['region'],
                    location: [js["etidesStnName"], js["region"], js["state"]].join(", "),
                    lat: js['lat'],
                    lon: js['lon'],
                    url: PUBLIC_STATION_URL % [ js['stationId'] ],
                    provider: 'noaa'
                )
            end

            return stations
        end

        def tide_data_for(station, around)
            from  = beginning_of_window(around).strftime("%Y%m%d")
            to    = end_of_window(around).strftime("%Y%m%d")
            url   = "#{API_URL}/api/prod/datagetter?product=predictions&datum=MLLW&time_zone=gmt&interval=hilo&units=english&application=web_services&format=json&begin_date=#{from}&end_date=#{to}&station=#{station.id}"

            logger.info "getting tide data from #{url}"

            return nil unless json = get_url(url)

            logger.debug "parsing tide prediction list for #{station.id} from API #{API_URL}"
            data = JSON.parse(json)["predictions"] rescue []

            return data.map do |jt|
                time = DateTime.parse(jt["t"])
                Models::TideData.new(
                    type: jt["type"] == "H" ? "High" : "Low",
                    units: "ft",
                    prediction: jt["v"].to_f,
                    time: time,
                    url: station.url + "&bdate=#{time.strftime("%Y%m%d")}"
                )
            end
        end
    end
end

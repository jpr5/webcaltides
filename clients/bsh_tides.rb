require_relative 'base'
require_relative '../models/station'
require_relative '../models/tide_data'

module Clients
    # Bundesamt für Seeschifffahrt und Hydrographie (BSH) -- official German tide predictions.
    #
    # BSH publishes its high/low water (HW/NW) predictions as static JSON behind
    # gezeiten.bsh.de: one overview of all gauges, plus one file per gauge holding the current
    # year and (from around August) the next year.  The files are only regenerated a few times a
    # year.  How often we fetch them:
    # - station list: once per quarter (WebCalTides caches it to disk); if it fails, again after
    #   WebCalTides::TIDE_STATIONS_RETRY (1h)
    # - a gauge file: once per station per requested month (WebCalTides caches each successful
    #   result to disk); after a 404, an unusable file or an empty window, not again for
    #   UNAVAILABLE_RETRY (6h, in memory, per process)
    # - exception: a future month that would be served without a year that becomes publishable
    #   by then is not cached at all, so it is fetched again on every request until then
    #
    # Terms (BSH Entgeltverzeichnis, Anlage 4 + AGB 5(10)): use and publication are free and need
    # no written consent, but (1) every presentation must credit the source in BSH's format, (2)
    # data for a calendar year may not be published before 1 August of the previous year, and (3)
    # the AGB bar use for navigation.
    #
    # Times are passed through unchanged, only normalized to UTC.  BSH timestamps are NOT local
    # time: every one carries a fixed +01:00 (MEZ), summer included -- BSH never applies DST
    # (e.g. 2026-09-29 05:39:00+01:00, which gezeiten.bsh.de shows as 06:39 MESZ).  We honour the
    # explicit offset, so the instant is right.  Do not "fix" this with Europe/Berlin DST rules:
    # that would shift every summer event by an hour.
    class BshTides < Base

        API_URL            = 'https://gezeiten.bsh.de/data'
        PUBLIC_STATION_URL = "https://gezeiten.bsh.de/%s"

        # AGB 5(10): "Datenquelle: Datensatzbezeichnung ©, Bundesamt für Seeschifffahrt und
        # Hydrographie, Ort, Jahr"
        ATTRIBUTION = "Datenquelle: Gezeitenvorausberechnungen ©, Bundesamt für Seeschifffahrt und Hydrographie, Hamburg, %s"

        DISCLAIMER = "NOT FOR NAVIGATION. Official tide predictions of the Bundesamt für Seeschifffahrt und Hydrographie (BSH), not corrected by Notices to Mariners (Nachrichten für Seefahrer). BSH gives no warranty for this information (Das BSH übernimmt für die angegebenen Informationen keine Gewähr)."

        include TimeWindow

        def initialize(logger)
            super
            @unavailable       = {}
            @unavailable_mutex = Mutex.new
        end

        # Get a full year (1 month behind + now + 11 ahead), bounded by what BSH has published
        self.window_size = 13.months

        # BSH data files are split by calendar year in MEZ (+01:00, no DST), so a dataset year can
        # differ from the UTC year of a time near midnight on 1 January.
        DATASET_UTC_OFFSET = '+01:00'

        def self.attribution(years)
            years = Array(years).compact.uniq.sort
            ATTRIBUTION % [ years.length > 1 ? "#{years.first}-#{years.last}" : years.first || Time.current.utc.year ]
        end

        # Which BSH dataset (calendar year) a time belongs to
        def self.dataset_year(time)
            time.to_datetime.new_offset(DATASET_UTC_OFFSET).year
        end

        # Dataset years a feed generated now serves: those in the data window that may be published
        def self.served_years(now: Time.current.utc)
            client = new(nil)
            years  = dataset_year(client.beginning_of_window(now))..dataset_year(client.end_of_window(now))
            years.select { |year| publishable?(year, now: now) }
        end

        # The BSH per-gauge notices (e.g. times from a fixed offset to a reference gauge, heights
        # affected by river flow), as one sentence list.  nil when there are none.
        def self.notice_text(notices)
            notices = Array(notices).map { |n| n.to_s.strip }.reject(&:empty?).uniq
            return nil if notices.empty?
            "BSH notes (Hinweise): " + notices.map { |n| n.end_with?('.') ? n : "#{n}." }.join(' ')
        end

        # Feed-level credit + disclaimer (+ notices) for a set of BSH TideData
        def self.feed_description(tides)
            tides = Array(tides)
            [
                "#{attribution(tides.map(&:dataset_year))}. #{DISCLAIMER}",
                notice_text(tides.flat_map { |tide| Array(tide.notes) })
            ].compact.join(' ')
        end

        # Per-event credit (+ notices) for one BSH TideData
        def self.event_description(tide)
            ["#{attribution(tide.dataset_year)}. NOT FOR NAVIGATION.", notice_text(tide.notes)].compact.join(' ')
        end

        # Anlage 4: "Gezeitendaten eines Kalenderjahres dürfen erst ab dem 1. August des Vorjahres
        # über das Internet (Webseiten, Apps) veröffentlicht werden."
        def self.publishable?(year, now: Time.current.utc)
            now >= Time.utc(year - 1, 8, 1)
        end

        # Station id = the stem of the gauge's BSH data file: 'DE_' + bshnr left-padded with '_'
        # to 5 characters, so 717P -> DE__717P and 3015P -> DE_3015P (a few gauges have 5-char
        # numbers).  tide_data_for fetches <id>_tides.json.
        def self.station_id_for(bshnr)
            "DE_#{bshnr.rjust(5, '_')}"
        end

        def tide_stations
            url = "#{API_URL}/tides_overview.json"

            logger.info "getting tide station list from #{url}"

            unless json = get_url(url)
                logger.error "!! got no BSH tide station list from #{url}"
                return nil
            end

            # nil (not []) on a broken list, so the caller doesn't cache "no German gauges" as healthy
            logger.debug "parsing tide station list from API #{API_URL}"
            begin
                data = JSON.parse(json).fetch("gauges")
                raise TypeError, "gauges is a #{data.class}" unless data.is_a?(Array)
            rescue JSON::ParserError, KeyError, TypeError, NoMethodError => e
                logger.error "!! BSH tide station list at #{url} unparseable (#{e.class}: #{e.message}), body starts #{json[0, 100].inspect}"
                return nil
            end

            if data.empty?
                logger.error "!! BSH tide station list at #{url} has no gauges"
                return nil
            end

            stations = data.filter_map do |js|
                unless js.is_a?(Hash) && js['bshnr'].is_a?(String) && js['bshnr'].present?
                    logger.warn "skipping BSH gauge without bshnr: #{js.inspect[0, 200]}"
                    next
                end

                Models::Station.new(
                    name: js['station_name'],
                    alternate_names: [js['seo_id']],
                    id: self.class.station_id_for(js['bshnr']),
                    public_id: js['bshnr'],
                    region: 'Germany',
                    location: [js['station_name'], 'Germany'].join(", "),
                    lat: js['latitude'],
                    lon: js['longitude'],
                    url: PUBLIC_STATION_URL % [ js['seo_id'] ],
                    provider: 'bsh'
                )
            end

            return stations
        end

        # How long to wait before fetching a gauge's file again after it was missing or unusable.
        # BSH regenerates its files only a few times a year, so retrying sooner mostly re-downloads
        # the same broken or missing file; waiting longer would hide a transient 404 for too long.
        UNAVAILABLE_RETRY = 6.hours

        TIDE_TYPES = { 'HW' => 'High', 'NW' => 'Low' }.freeze

        # BSH timestamps always carry an explicit offset; without one DateTime.parse would read UTC
        TIMESTAMP_WITH_OFFSET = /\A\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:?\d{2})\z/

        def tide_data_for(station, around)
            from  = beginning_of_window(around)
            to    = end_of_window(around)
            url   = "#{API_URL}/#{station.id}_tides.json"

            # Unusable file (any window), or nothing in this window
            window_key = "#{station.id}@#{around.utc.strftime('%Y%m')}"
            if (until_time = unavailable_until(station.id) || unavailable_until(window_key))
                logger.debug "skipping BSH tide data for #{station.id}: unavailable, next try after #{until_time}"
                return nil
            end

            logger.info "getting tide data from #{url}"

            begin
                unless json = get_url(url)
                    logger.error "!! got no BSH tide data from #{url}"
                    return unavailable!(station.id)
                end
            rescue Mechanize::ResponseCodeError => e
                if e.response_code == "404"
                    logger.warn "404 for station #{station.id}, skipping (may be temporary), next try in #{UNAVAILABLE_RETRY.inspect}"
                    return unavailable!(station.id)
                end
                raise
            end

            logger.debug "parsing tide predictions for #{station.id} from API #{API_URL}"
            begin
                data = JSON.parse(json).fetch("years")
                raise TypeError, "years is a #{data.class}" unless data.is_a?(Array) && data.all?(Hash)
            rescue JSON::ParserError, KeyError, TypeError, NoMethodError => e
                logger.error "!! BSH tide data for station #{station.id} at #{url} unparseable (#{e.class}: #{e.message}), body starts #{json[0, 100].inspect}"
                return unavailable!(station.id)
            end

            # Years we must not publish yet (Anlage 4), but would be inside this window by the end
            # of the requested month.  See below.
            withheld = []

            tides = data.flat_map(&:to_a).flat_map do |year, yd|
                unless self.class.publishable?(year.to_i)
                    # Expected every fetch from January through July, so not worth a warning
                    logger.debug "skipping #{year} BSH data for station #{station.id}: not publishable before 1 August #{year.to_i - 1}"
                    withheld << year.to_i if self.class.publishable?(year.to_i, now: around.utc.end_of_month) && Time.utc(year.to_i) <= to
                    next []
                end

                tides_for_year(station, year, yd || {})
            end

            # A future month's result is cached for that whole month (here and as the ICS feed), so
            # if a year becomes publishable by then, caching it without that year would hide the
            # year once it's out.  Don't serve the month until it can be served complete.
            if withheld.any?
                logger.info "not serving BSH tide data for #{station.id} around #{around.utc.strftime('%Y-%m')} yet: #{withheld.join(', ')} not publishable until 1 August #{withheld.min - 1}"
                return nil
            end

            tides = tides.select { |td| td.time >= from && td.time <= to }.sort_by(&:time)

            if tides.empty?
                logger.error "!! no BSH tide data for station #{station.id} between #{from.strftime('%Y-%m-%d')} and #{to.strftime('%Y-%m-%d')} (years: #{data.flat_map(&:keys).join(', ')})"
                return unavailable!(window_key)
            end

            return tides
        end

        private

        def tides_for_year(station, year, yd)
            prediction = yd['hwnw_prediction'] || {}
            events     = Array(prediction['data'])
            heights    = yd['has_height'] != false && events.any? { |jt| jt.is_a?(Hash) && jt['height'] }
            offset     = chart_datum_offset(prediction['level'], yd)
            notes      = Array(yd['notice']).map(&:to_s).reject(&:blank?).presence

            if heights && offset.nil?
                logger.warn "omitting heights of #{year} BSH data for station #{station.id}: unknown datum (level #{prediction['level'].inspect}, SKN (ueber PNP) #{yd['SKN (ueber PNP)'].inspect})"
            end

            skipped = []
            tides = events.filter_map do |jt|
                type = jt.is_a?(Hash) && TIDE_TYPES[jt['type']]
                time = parse_timestamp(jt['timestamp']) if type

                unless type && time
                    skipped << jt
                    next
                end

                height = (heights && jt['height'] && offset) ? ((jt['height'] - offset) / 100.0).round(2) : nil

                Models::TideData.new(
                    type: type,
                    units: "m",
                    prediction: height,
                    time: time,
                    url: station.url,
                    dataset_year: year.to_i,
                    notes: notes
                )
            end

            if skipped.any?
                logger.warn "skipping #{skipped.length} BSH events of #{year} for station #{station.id} with unknown type or bad timestamp, e.g. #{skipped.first.inspect[0, 200]}"
            end

            return tides
        end

        # Same instant as published by BSH (fixed +01:00, no DST -- see the class comment), just
        # normalized to UTC.  nil if unusable.
        def parse_timestamp(timestamp)
            return nil unless timestamp.is_a?(String) && timestamp.match?(TIMESTAMP_WITH_OFFSET)
            DateTime.parse(timestamp).new_offset(0)
        rescue Date::Error
            nil
        end

        # BSH heights are in cm above the gauge zero (PNP) or chart datum (SKN).  Report them above
        # chart datum (SKN), which is also what gezeiten.bsh.de shows by default.  nil means we
        # can't determine the datum (or the gauge has no heights), so heights are omitted.
        def chart_datum_offset(level, year_data)
            case level
            when 'SKN' then 0
            when 'PNP' then year_data['SKN (ueber PNP)']
            end
        end

        # Negative cache for gauges (or gauge + month) without usable data, so we don't refetch on
        # every request (WebCalTides only caches successful results).  Per process, in memory.
        def unavailable!(key)
            @unavailable_mutex.synchronize { @unavailable[key] = Time.current.utc + UNAVAILABLE_RETRY }
            return nil
        end

        def unavailable_until(key)
            @unavailable_mutex.synchronize do
                until_time = @unavailable[key] or return nil
                return until_time if Time.current.utc < until_time
                @unavailable.delete(key)
                nil
            end
        end

    end
end

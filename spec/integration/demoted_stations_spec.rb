# frozen_string_literal: true

require 'tmpdir'

# Vigo has two TICON stations 0.6 km apart, so search shows them as two cards.  T6e11ade has the
# constants of the UHSLC Vigo record (1943-1990), whose timestamps are 1 h early.  Tdce40e9 has the
# constants of the CMEMS VigoTG record (1992-2021).  T6e11ade's high and low waters come about
# 60 min before Tdce40e9's, so T6e11ade is demoted: it stays in the results, after Tdce40e9, with a
# warning.  These run on the shipped TICON data, as the app serves it.
RSpec.describe 'Demoted stations', type: :request do
    include Rack::Test::Methods

    def app
        Server
    end

    TICON_JSON = File.expand_path('../../data/ticon.json', __dir__)

    before(:all) do
        @dir    = Dir.mktmpdir
        @client = Clients::Harmonics.new(Logger.new('/dev/null'))
        @client.instance_variable_set(:@engine, Harmonics::Engine.new(Logger.new('/dev/null'), @dir))
        @harmonics_stations = @client.tide_stations
    end

    after(:all) { FileUtils.rm_rf(@dir) }

    # Far from Vigo (no grouping with it), but found by the same searches
    let(:unrelated) do
        build_station(name: 'Vigo Test Harbour', id: 'T0000001', public_id: 'T0000001', region: 'ESP',
                      provider: 'ticon', lat: 42.30, lon: -8.80)
    end

    before do
        allow(WebCalTides).to receive(:tide_stations).and_return(@harmonics_stations + [unrelated])
        allow(WebCalTides).to receive(:current_stations).and_return([])
    end

    # Tide station ids in the order the result page shows its cards
    def card_ids(searchtext)
        post '/', { searchtext: searchtext, units: 'metric' }
        expect(last_response.status).to eq(200)
        last_response.body.scan(/data-station-id="([^"]+)"/).flatten
    end

    describe 'the list' do
        it 'names only stations in the shipped data/ticon.json' do
            shipped = JSON.parse(File.read(TICON_JSON))['stations'].map { |s| s['id'] }
            expect(WebCalTides::DEMOTED_STATIONS.keys - shipped).to be_empty
            expect(WebCalTides::DEMOTED_STATIONS.keys - @harmonics_stations.map(&:id)).to be_empty
        end

        it 'gives a better station, a warning and the evidence for every entry' do
            expect(WebCalTides::DEMOTED_STATIONS).not_to be_empty
            WebCalTides::DEMOTED_STATIONS.each do |id, entry|
                expect(entry).to be_a(Hash), "#{id}: #{entry.inspect}"
                expect(entry.values_at(:better, :warning, :evidence)).to all(be_a(String).and(be_present)), "#{id}: #{entry.inspect}"
                expect(@harmonics_stations.map(&:id)).to include(entry[:better])
            end
        end

        it 'has both Vigo stations in the shipped TICON data' do
            vigo = @harmonics_stations.select { |s| s.name == 'Vigo, ESP' }.map(&:id)
            expect(vigo).to contain_exactly('T6e11ade', 'Tdce40e9')
        end
    end

    describe 'startup check' do
        it 'warns about a demoted id that is not in the loaded stations' do
            stub_const('WebCalTides::DEMOTED_STATIONS', { 'T6e11ade' => {}, 'Tgone000' => {} })
            allow(WebCalTides.logger).to receive(:warn)

            WebCalTides.check_demoted_stations

            expect(WebCalTides.logger).to have_received(:warn).with(/Tgone000/).once
            expect(WebCalTides.logger).not_to have_received(:warn).with(/T6e11ade/)
        end
    end

    describe 'search' do
        it 'lists Tdce40e9 first and T6e11ade last for a search by name' do
            expect(card_ids('vigo')).to eq(%w[Tdce40e9 T0000001 T6e11ade])
        end

        it 'lists Tdce40e9 first and T6e11ade last for a GPS search at Vigo' do
            expect(card_ids('42.2333, -8.7333')).to eq(%w[Tdce40e9 T0000001 T6e11ade])
        end

        it 'shows the warning on the demoted card only' do
            post '/', { searchtext: 'vigo', units: 'metric' }
            cards = last_response.body.split('class="station-card ').drop(1)
            by_id = cards.to_h { |c| [c[/data-station-id="([^"]+)"/, 1], c] }

            expect(by_id['T6e11ade']).to include('demotion-warning', 'about 1 h early', 'Tdce40e9')
            expect(by_id['Tdce40e9']).not_to include('demotion-warning')
            expect(by_id['T0000001']).not_to include('demotion-warning')
        end

        it 'marks the demoted source in the source picker' do
            demoted = @harmonics_stations.find { |s| s.id == 'T6e11ade' }
            xtide   = build_station(name: 'Vigo', id: 'X0000001', public_id: 'X0000001', provider: 'xtide',
                                    lat: demoted.lat, lon: demoted.lon)
            allow(WebCalTides).to receive(:tide_stations).and_return(@harmonics_stations + [xtide])

            post '/', { searchtext: 'vigo', units: 'metric' }
            card    = last_response.body.split('class="station-card ').drop(1).find { |c| c.include?('data-station-id="X0000001"') }
            sources = JSON.parse(CGI.unescapeHTML(card[/sources: (\[.*?\]),\s*webcalBase/m, 1]))

            expect(sources.map { |s| s['id'] }).to eq(%w[X0000001 T6e11ade])
            expect(sources.last['warning']).to include('about 1 h early', 'Tdce40e9')
            expect(sources.first['warning']).to be_nil
            expect(card).to include('source.warning')
        end
    end

    describe 'feed' do
        before do
            Timecop.freeze(Time.utc(2026, 11, 15, 12))
            allow(Server.settings).to receive(:cache_dir).and_return(@dir)
            allow(WebCalTides).to receive(:cleanup_if_month_changed)
            allow(WebCalTides).to receive(:tide_clients).and_wrap_original do |m, *args|
                args.first.to_s.in?(%w[xtide ticon]) ? @client : m.call(*args)
            end
        end

        it 'serves T6e11ade with the warning on the calendar and on every tide event' do
            get '/tides/T6e11ade.ics?solar=0'
            expect(last_response.status).to eq(200)

            cal = Icalendar::Calendar.parse(last_response.body).first
            expect(cal.events).not_to be_empty
            expect(cal.description.to_s).to include('about 1 h early', 'Tdce40e9')
            expect(cal.events.map { |e| e.description.to_s }).to all(include('about 1 h early'))
        end

        it 'does not serve a feed cached before the demotion' do
            stale = "#{@dir}/tides_v#{Models::TideData.version}_T6e11ade_202611" \
                    "#{WebCalTides.harmonics_cache_key(WebCalTides.tide_station_for('T6e11ade'))}_imperial_0_0.ics"
            File.write(stale, "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:stale\r\nEND:VCALENDAR\r\n")

            get '/tides/T6e11ade.ics?solar=0'
            expect(last_response.status).to eq(200)
            expect(last_response.body).not_to include('PRODID:stale')
            expect(last_response.body).to include('about 1 h early')
        end

        it 'serves Tdce40e9 without the warning' do
            get '/tides/Tdce40e9.ics?solar=0'
            expect(last_response.status).to eq(200)
            expect(last_response.body).not_to include('1 h early')
        end
    end

    describe 'grouping' do
        it 'never makes a demoted station the primary, even over a lower-ranked provider' do
            stub_const('WebCalTides::DEMOTED_STATIONS', { 'X0000001' => {} })
            demoted = build_station(id: 'X0000001', public_id: 'X0000001', provider: 'xtide')
            ticon   = build_station(id: 'T0000002', public_id: 'T0000002', provider: 'ticon')

            groups = WebCalTides.group_stations_by_proximity([demoted, ticon])
            expect(groups.length).to eq(1)
            expect(groups.first.primary.id).to eq('T0000002')
            expect(groups.first.alternatives.map(&:id)).to eq(['X0000001'])
        end

        it 'moves a group of demoted current stations after the others, which keep their order' do
            stub_const('WebCalTides::DEMOTED_STATIONS', { 'C_BAD' => {} })
            current = ->(id, bid, lat, depth) {
                build_station(id: id, bid: bid, public_id: bid, provider: 'xtide', lat: lat, lon: -70.0, depth: depth)
            }
            stations = [current.('C_BAD', 'C_BAD_10', 40.0, 10), current.('C_BAD', 'C_BAD_20', 40.0, 20),
                        current.('C_A', 'C_A', 41.0, nil), current.('C_B', 'C_B', 42.0, nil)]

            groups = WebCalTides.group_search_results(stations, match_depth: false)
            expect(groups.map { |g| g.primary.bid }).to eq(%w[C_A C_B C_BAD_10])
            expect(groups.last.alternatives.map(&:bid)).to eq(['C_BAD_20'])
        end
    end
end

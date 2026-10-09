# frozen_string_literal: true

require 'tmpdir'

# Vigo has two TICON stations 0.6 km apart, so search shows them as two cards.  T6e11ade comes from
# the UHSLC Vigo record (1943-1990), whose timestamps are 1 h early, so its phases are about 60 min
# behind Tdce40e9 (from the IEO record).  T6e11ade is demoted: it stays in the results, after
# Tdce40e9.  This runs the search on the shipped TICON data, as the app serves it.
RSpec.describe 'Demoted stations', type: :request do
    include Rack::Test::Methods

    def app
        Server
    end

    before(:all) do
        @dir     = Dir.mktmpdir
        client = Clients::Harmonics.new(Logger.new('/dev/null'))
        client.instance_variable_set(:@engine, Harmonics::Engine.new(Logger.new('/dev/null'), @dir))
        @harmonics_stations = client.tide_stations
    end

    after(:all) { FileUtils.rm_rf(@dir) }

    before do
        allow(WebCalTides).to receive(:tide_stations).and_return(@harmonics_stations)
        allow(WebCalTides).to receive(:current_stations).and_return([])
    end

    # Tide station ids in the order the result page shows its cards
    def card_ids(searchtext)
        post '/', { searchtext: searchtext, units: 'metric' }
        expect(last_response.status).to eq(200)
        last_response.body.scan(/data-station-id="([^"]+)"/).flatten
    end

    it 'has both Vigo stations in the shipped TICON data' do
        vigo = @harmonics_stations.select { |s| s.name == 'Vigo, ESP' }.map(&:id)
        expect(vigo).to contain_exactly('T6e11ade', 'Tdce40e9')
    end

    it 'lists Tdce40e9 first and T6e11ade after it for a search by name' do
        expect(card_ids('vigo')).to eq(%w[Tdce40e9 T6e11ade])
    end

    it 'lists Tdce40e9 first and T6e11ade after it for a GPS search at Vigo' do
        expect(card_ids('42.2333, -8.7333')).to eq(%w[Tdce40e9 T6e11ade])
    end

    it 'keeps T6e11ade subscribable' do
        expect(WebCalTides.station_ids).to include('T6e11ade', 'Tdce40e9')
    end

    it 'gives the evidence for each demoted station' do
        expect(WebCalTides::DEMOTED_STATIONS['T6e11ade']).to include('UHSLC', '1 h')
    end

    it 'never makes a demoted station the primary of a group' do
        demoted = @harmonics_stations.find { |s| s.id == 'T6e11ade' }
        other   = build_station(id: 'X0000000', public_id: 'X0000000', provider: 'ticon', lat: demoted.lat, lon: demoted.lon)

        groups = WebCalTides.group_stations_by_proximity([demoted, other])
        expect(groups.length).to eq(1)
        group = groups.first
        expect(group.primary.id).to eq('X0000000')
        expect(group.alternatives.map(&:id)).to eq(['T6e11ade'])
    end
end

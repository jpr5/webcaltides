# frozen_string_literal: true

RSpec.describe 'GET /api/stations/autocomplete', type: :api do
  include Rack::Test::Methods

  before do
    allow(WebCalTides).to receive(:tide_stations).and_return([
      build_station(name: 'Boston Harbor', region: 'Massachusetts, USA'),
      build_station(name: 'Boston Inner Harbor', region: 'Massachusetts, USA'),
      build_station(name: 'Portland', region: 'Maine, USA')
    ])

    allow(WebCalTides).to receive(:current_stations).and_return([
      build_station(name: 'Boston Harbor Entrance', region: 'Massachusetts, USA', depth: 10),
      build_station(name: 'Cape Cod Canal', region: 'Massachusetts, USA', depth: 15)
    ])
  end

  context 'with valid query' do
    it 'returns matching stations' do
      get '/api/stations/autocomplete', q: 'boston'

      expect(last_response).to be_ok
      expect(last_response.content_type).to include('application/json')

      results = JSON.parse(last_response.body)['results']
      expect(results.length).to eq(3)  # 2 tide + 1 current
    end

    it 'returns station name and region' do
      get '/api/stations/autocomplete', q: 'boston'

      results = JSON.parse(last_response.body)['results']
      expect(results.first).to include('name', 'region')
    end

    it 'returns station type' do
      get '/api/stations/autocomplete', q: 'boston'

      results = JSON.parse(last_response.body)['results']
      types = results.map { |r| r['type'] }
      expect(types).to include('tide', 'current')
    end

    it 'searches by region' do
      get '/api/stations/autocomplete', q: 'maine'

      results = JSON.parse(last_response.body)['results']
      expect(results.length).to eq(1)
      expect(results.first['name']).to eq('Portland')
    end

    it 'deduplicates by name and region' do
      get '/api/stations/autocomplete', q: 'boston'

      results = JSON.parse(last_response.body)['results']
      unique_pairs = results.map { |r| [r['name'], r['region']] }.uniq
      expect(unique_pairs.length).to eq(results.length)
    end

    it 'limits results to 10' do
      # Mock many stations
      many_stations = (1..20).map do |i|
        build_station(name: "Boston Station #{i}", region: 'Massachusetts, USA')
      end
      allow(WebCalTides).to receive(:tide_stations).and_return(many_stations)
      allow(WebCalTides).to receive(:current_stations).and_return([])

      get '/api/stations/autocomplete', q: 'boston'

      results = JSON.parse(last_response.body)['results']
      expect(results.length).to be <= 10
    end
  end

  context 'with short query' do
    it 'returns empty results for single character' do
      get '/api/stations/autocomplete', q: 'b'

      results = JSON.parse(last_response.body)['results']
      expect(results).to be_empty
    end

    it 'returns results for 2+ characters' do
      get '/api/stations/autocomplete', q: 'bo'

      results = JSON.parse(last_response.body)['results']
      expect(results).not_to be_empty
    end
  end

  context 'with empty query' do
    it 'returns empty results' do
      get '/api/stations/autocomplete', q: ''

      results = JSON.parse(last_response.body)['results']
      expect(results).to be_empty
    end
  end

  context 'with no matches' do
    it 'returns empty results' do
      get '/api/stations/autocomplete', q: 'xyz123'

      results = JSON.parse(last_response.body)['results']
      expect(results).to be_empty
    end
  end
end

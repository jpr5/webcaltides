# frozen_string_literal: true

RSpec.describe 'Tide station cache and tide data cache', :aggregate_failures do
    include Rack::Test::Methods

    def app
        Server
    end

    let(:noaa_station) { build_station(id: 'NOAA1', public_id: 'NOAA1', name: 'Boston', provider: 'noaa') }
    let(:bsh_station)  { build_station(id: 'DE__717P', public_id: '717P', name: 'Cranz', provider: 'bsh', lat: 53.5, lon: 9.8) }

    let(:noaa) { double('noaa', tide_stations: [noaa_station]) }
    let(:bsh)  { double('bsh', tide_stations: [bsh_station]) }
    let(:clients) { { noaa: noaa, bsh: bsh } }

    around do |example|
        with_test_cache_dir do |dir|
            @cache_dir = dir
            WebCalTides.instance_variable_set(:@tide_stations, nil)
            WebCalTides.instance_variable_set(:@tide_stations_retry_at, nil)
            example.run
        ensure
            WebCalTides.instance_variable_set(:@tide_stations, nil)
            WebCalTides.instance_variable_set(:@tide_stations_retry_at, nil)
        end
    end

    before do
        allow(WebCalTides).to receive(:harmonics_checksum).and_return('cafebabe')
        allow(WebCalTides).to receive(:tide_clients) { |provider = nil| provider ? clients[provider.to_sym] : clients }
    end

    def station_cache_files
        Dir.glob("#{@cache_dir}/tide_stations_*.json")
    end

    describe 'station list build' do
        it 'names the cache file after the set of tide providers' do
            with_bsh    = WebCalTides.tide_station_cache_file
            clients.delete(:bsh)
            without_bsh = WebCalTides.tide_station_cache_file

            expect(with_bsh).not_to eq(without_bsh)
            expect(File.basename(with_bsh)).to match(/\Atide_stations_v\d+_20\d\dQ\d_cafebabe_hs\d+_\h{8}\.json\z/)
        end

        [
            ['raises an HTTP error',    -> { raise Mechanize::ResponseCodeError.new(Struct.new(:code).new('503'), '503') }],
            ['is unreachable',          -> { raise Errno::ECONNREFUSED }],
            ['refuses the connection',  -> { raise Net::HTTP::Persistent::Error, 'connection refused: 127.0.0.1:9' }],
            ['returns no station list', -> { nil }]
        ].each do |what, failure|
            it "still lists the other providers' stations when BSH #{what}" do
                allow(bsh).to receive(:tide_stations, &failure)

                expect(WebCalTides.tide_stations.map(&:id)).to eq(['NOAA1'])
                expect(WebCalTides.find_tide_stations(by: ['boston']).map(&:id)).to eq(['NOAA1'])
            end
        end

        it 'does not cache an incomplete list for the quarter, and retries the failed build later' do
            allow(bsh).to receive(:tide_stations).and_raise(Errno::ECONNREFUSED)

            start = Time.current.utc
            expect(WebCalTides.tide_stations.map(&:id)).to eq(['NOAA1'])
            expect(station_cache_files).to be_empty

            # Before the retry interval: still the degraded in-memory list, no refetch
            expect(WebCalTides.tide_stations.map(&:id)).to eq(['NOAA1'])
            expect(bsh).to have_received(:tide_stations).once

            allow(bsh).to receive(:tide_stations).and_return([bsh_station])
            Timecop.freeze(start + WebCalTides::TIDE_STATIONS_RETRY + 1) do
                expect(WebCalTides.tide_stations.map(&:id)).to contain_exactly('NOAA1', 'DE__717P')
                expect(station_cache_files).to eq([WebCalTides.tide_station_cache_file])
            end
        end

        it 'does not cache an empty station list from a provider, and retries it later' do
            # e.g. NOAA answering 200 with a maintenance page parses to []
            allow(bsh).to receive(:tide_stations).and_return([])

            start = Time.current.utc
            expect(WebCalTides.tide_stations.map(&:id)).to eq(['NOAA1'])
            expect(station_cache_files).to be_empty

            allow(bsh).to receive(:tide_stations).and_return([bsh_station])
            Timecop.freeze(start + WebCalTides::TIDE_STATIONS_RETRY + 1) do
                expect(WebCalTides.tide_stations.map(&:id)).to contain_exactly('NOAA1', 'DE__717P')
                expect(station_cache_files).to eq([WebCalTides.tide_station_cache_file])
            end
        end

        it 'does not cache an empty list when every provider comes back empty' do
            allow(noaa).to receive(:tide_stations).and_return([])
            allow(bsh).to receive(:tide_stations).and_return([])

            expect(WebCalTides.tide_stations).to eq([])
            expect(station_cache_files).to be_empty
        end

        it 'keeps the last list it had when a retry comes back empty' do
            allow(bsh).to receive(:tide_stations).and_raise(Errno::ECONNREFUSED)
            start = Time.current.utc
            WebCalTides.tide_stations

            allow(noaa).to receive(:tide_stations).and_return([])
            Timecop.freeze(start + WebCalTides::TIDE_STATIONS_RETRY + 1) do
                expect(WebCalTides.tide_stations.map(&:id)).to eq(['NOAA1'])
                expect(station_cache_files).to be_empty
            end
        end

        it 'rebuilds an empty cached station list instead of serving it for the quarter' do
            cache_file = WebCalTides.tide_station_cache_file
            File.write(cache_file, '[]')

            expect(WebCalTides.tide_stations.map(&:id)).to contain_exactly('NOAA1', 'DE__717P')
            expect(JSON.parse(File.read(cache_file)).length).to eq(2)
        end

        it 'does not persist an incomplete list when a station is removed' do
            allow(bsh).to receive(:tide_stations).and_raise(Errno::ECONNREFUSED)
            WebCalTides.tide_stations

            WebCalTides.remove_tide_station('NOAA1')
            expect(station_cache_files).to be_empty
        end

        it 'keeps serving the incomplete list to other requests while one rebuilds it' do
            allow(bsh).to receive(:tide_stations).and_raise(Errno::ECONNREFUSED)
            start = Time.current.utc
            WebCalTides.tide_stations

            # The retry hangs until released, as a provider that times out would
            started, release = Queue.new, Queue.new
            allow(bsh).to receive(:tide_stations) { started << true; release.pop(timeout: 5); [bsh_station] }

            Timecop.travel(start + WebCalTides::TIDE_STATIONS_RETRY + 1) do
                rebuild = Thread.new { WebCalTides.tide_stations }
                started.pop(timeout: 5) or raise 'the retry never reached the provider'

                reader = Thread.new { WebCalTides.tide_stations.map(&:id) }
                expect(reader.join(2)&.value).to eq(['NOAA1'])

                release << :go
                rebuild.join(5) or raise 'the retry did not finish'
                expect(rebuild.value.map(&:id)).to contain_exactly('NOAA1', 'DE__717P')
            ensure
                release << :go
                [rebuild, reader].compact.each { |t| t.join(2) || t.kill }
            end
        end

        it 'keeps the complete list when a station is removed while a retry finishes the rebuild' do
            chs_station = build_station(id: 'CHS1', public_id: 'CHS1', name: 'Halifax', provider: 'chs')
            allow(noaa).to receive(:tide_stations).and_return([noaa_station, chs_station])
            allow(bsh).to receive(:tide_stations).and_raise(Errno::ECONNREFUSED)
            start = Time.current.utc
            WebCalTides.tide_stations

            # The retry succeeds, then holds while it reads the complete list back from the cache
            allow(bsh).to receive(:tide_stations).and_return([bsh_station])
            reading, release = Queue.new, Queue.new
            read = File.method(:read)

            Timecop.travel(start + WebCalTides::TIDE_STATIONS_RETRY + 1) do
                # Named after the travel: the retry may land in the next quarter
                cache_file = WebCalTides.tide_station_cache_file
                allow(File).to receive(:read).and_call_original
                allow(File).to receive(:read).with(cache_file) do |path|
                    reading << true
                    release.pop(timeout: 5)
                    read.call(path)
                end

                rebuild = Thread.new { WebCalTides.tide_stations }
                reading.pop(timeout: 5) or raise 'the retry never read the rebuilt cache'

                # Another request finds a CHS station with no data and nukes it, as chs_tides.rb does
                remover = Thread.new { WebCalTides.remove_tide_station('CHS1') }
                deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 2
                sleep 0.01 until remover.status != 'run' || Process.clock_gettime(Process::CLOCK_MONOTONIC) > deadline
                # Still waiting on the lock the rebuild holds
                expect(remover.status).to eq('sleep')
                expect(WebCalTides.class_variable_get(:@@tide_stations_mutex)).to be_locked

                release << :go
                rebuild.join(5) or raise 'the retry did not finish'
                remover.join(5) or raise 'the removal did not finish'

                expect(WebCalTides.tide_stations.map(&:id)).to contain_exactly('NOAA1', 'DE__717P')
                expect(JSON.parse(read.call(cache_file)).map { |h| h['id'] }).to contain_exactly('NOAA1', 'DE__717P')
            ensure
                release << :go
                [rebuild, remover].compact.each { |t| t.join(2) || t.kill }
            end
        end

        {
            'truncated JSON'      => '[{"id":',
            'JSON null'           => 'null',
            'a non-station list'  => '[1]'
        }.each do |what, content|
            it "logs, replaces and recovers from a cache file holding #{what}" do
                # e.g. another worker or a full disk left a broken file
                cache_file = WebCalTides.tide_station_cache_file
                File.write(cache_file, content)
                allow(WebCalTides.logger).to receive(:error).and_call_original

                3.times do
                    expect(WebCalTides.tide_stations.map(&:id)).to contain_exactly('NOAA1', 'DE__717P')
                end
                expect(WebCalTides.logger).to have_received(:error).with(/unreadable tide station cache #{Regexp.escape(cache_file)}/).once
                expect(JSON.parse(File.read(cache_file)).map { |h| h['id'] }).to contain_exactly('NOAA1', 'DE__717P')
            end

            it "keeps serving the incomplete list, and retries later, when a retry finds a cache file holding #{what}" do
                allow(bsh).to receive(:tide_stations).and_raise(Errno::ECONNREFUSED)
                start = Time.current.utc
                WebCalTides.tide_stations

                Timecop.freeze(start + WebCalTides::TIDE_STATIONS_RETRY + 1) do
                    cache_file = WebCalTides.tide_station_cache_file
                    File.write(cache_file, content)
                    allow(WebCalTides.logger).to receive(:error).and_call_original

                    3.times do
                        expect(WebCalTides.tide_stations.map(&:id)).to eq(['NOAA1'])
                    end
                    expect(WebCalTides.logger).to have_received(:error).with(/unreadable tide station cache #{Regexp.escape(cache_file)}/).once
                    expect(WebCalTides.tide_stations_retry_due?).to be_falsey
                    expect(File.exist?(cache_file)).to be(false)
                end

                allow(bsh).to receive(:tide_stations).and_return([bsh_station])
                Timecop.freeze(start + 2 * WebCalTides::TIDE_STATIONS_RETRY + 2) do
                    expect(WebCalTides.tide_stations.map(&:id)).to contain_exactly('NOAA1', 'DE__717P')
                    expect(station_cache_files).to eq([WebCalTides.tide_station_cache_file])
                end
            end
        end

        it 'serves the fetched list uncached, and retries later, when even the rebuilt cache cannot be read' do
            cache_file = WebCalTides.tide_station_cache_file
            allow(File).to receive(:read).and_call_original
            allow(File).to receive(:read).with(cache_file).and_return('[{"id":')

            3.times do
                expect(WebCalTides.tide_stations.map(&:id)).to contain_exactly('NOAA1', 'DE__717P')
            end
            expect(noaa).to have_received(:tide_stations).twice
            expect(WebCalTides.tide_stations_retry_due?).to be_falsey
            expect(File.exist?(cache_file)).to be(false)
        end

        it "leaves other code versions' station caches on the shared cache volume" do
            # e.g. the previous release during a rolling deploy; old quarters go in cleanup_old_cache_files
            sibling = "#{@cache_dir}/tide_stations_v2_2026Q4_cafebabe.json"
            File.write(sibling, '[]')

            WebCalTides.tide_stations

            expect(station_cache_files).to contain_exactly(sibling, WebCalTides.tide_station_cache_file)
        end

        # The list and its degraded flag came from two engine calls.  When the engine's TICON retry
        # fell due between them, a list built without TICON was reported as complete and cached for
        # the quarter.
        context 'when the harmonics TICON retry falls due while the list is built' do
            let(:harmonics) { Clients::Harmonics.new(Logger.new('/dev/null')) }
            let(:clients)   { { harmonics: harmonics } }

            it 'does not cache the list built without TICON' do
                engine = ::Harmonics::Engine.new(Logger.new('/dev/null'), Dir.mktmpdir)
                harmonics.instance_variable_set(:@engine, engine)

                failed = 0
                allow(File).to receive(:read).and_call_original
                allow(File).to receive(:read).with(engine.ticon_file).and_wrap_original do |m, *args|
                    (failed += 1) <= 1 ? raise(Errno::EIO, engine.ticon_file) : m.call(*args)
                end
                # The retry falls due right after the client hands back the list
                allow(harmonics).to receive(:tide_stations).and_wrap_original do |m|
                    list = m.call
                    Timecop.travel(Time.now + ::Harmonics::Engine::STATIONS_RETRY + 1)
                    list
                end

                stations = WebCalTides.tide_stations
                expect(stations.count { |s| s.provider == 'ticon' }).to eq(0)
                expect(station_cache_files).to be_empty
            end
        end
    end

    describe 'tide data' do
        before { allow(bsh).to receive(:tide_data_for).and_return(tide_data) }

        context 'when the client returns an empty list' do
            let(:tide_data) { [] }

            it 'does not cache it' do
                expect(WebCalTides.tide_data_for(bsh_station)).to be_nil
                expect(Dir.glob("#{@cache_dir}/tides_*")).to be_empty
            end
        end

        context 'when the client has no data for the station' do
            let(:tide_data) { nil }

            before do
                allow(WebCalTides).to receive(:station_ids).and_return(['DE__717P'])
                allow(WebCalTides).to receive(:tide_station_for).with('DE__717P').and_return(bsh_station)
            end

            it 'returns no calendar rather than an empty one' do
                expect(WebCalTides.tide_calendar_for('DE__717P')).to be_nil
            end

            it 'answers the ICS feed with 404 and caches nothing' do
                get '/tides/DE__717P.ics'

                expect(last_response.status).to eq(404)
                expect(Dir.glob("#{@cache_dir}/*.ics") + Dir.glob("#{@cache_dir}/tides_*")).to be_empty
            end
        end
    end
end

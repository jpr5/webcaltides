# frozen_string_literal: true

# Harmonics-served caches (tide JSON, currents JSON, tide and currents ICS) are keyed on the
# TICON/XTide dataset (harmonics_checksum) and on the running engine's cache_key_component (engine
# version + resolved HARMONICS_NODAL), so a dataset, engine or flag change rebuilds only those caches.
# Agency caches keep their names.  Every name keeps "_YYYYMM" as the first "_20dddd" token followed
# by "_" or ".", which is what the monthly cleanup keys on.
RSpec.describe 'Harmonics cache keys' do
    let(:june) { Time.utc(2025, 6, 15, 12, 0, 0) }
    let(:checksum) { 'aaaaaaaa_bbbbbbbb' }

    before do
        freeze_time(june)
        allow(WebCalTides).to receive(:harmonics_checksum) { checksum }
        allow(WebCalTides).to receive(:harmonics_engine_key).and_return('hA3tcd')
    end

    def names(dir, glob = '*')
        Dir.glob("#{dir}/#{glob}").map { |f| File.basename(f) }.sort
    end

    def flip_flag(to = 'hA3legacy')
        allow(WebCalTides).to receive(:harmonics_engine_key).and_return(to)
    end

    describe 'tide JSON' do
        let(:client) { double('tide client') }

        before do
            allow(client).to receive(:tide_data_for) { [build_tide_data(time: DateTime.now)] }
            allow(WebCalTides).to receive(:tide_clients).and_call_original
            allow(WebCalTides).to receive(:tide_clients).with('ticon').and_return(client)
            allow(WebCalTides).to receive(:tide_clients).with('xtide').and_return(client)
        end

        %w[ticon xtide].each do |provider|
            it "writes a new #{provider} file when the engine flag flips, and does not serve the old one" do
                with_test_cache_dir do |dir|
                    station = build_station(id: 'TICON-1', provider: provider)
                    WebCalTides.tide_data_for(station)
                    first = names(dir)

                    flip_flag
                    WebCalTides.tide_data_for(station)

                    expect(first.size).to eq(1)
                    expect(names(dir).size).to eq(2)
                    expect(client).to have_received(:tide_data_for).twice
                end
            end
        end

        it 'writes a new file when the harmonics dataset changes' do
            with_test_cache_dir do |dir|
                station = build_station(id: 'TICON-1', provider: 'ticon')
                WebCalTides.tide_data_for(station)
                allow(WebCalTides).to receive(:harmonics_checksum).and_return('cccccccc_dddddddd')
                WebCalTides.tide_data_for(station)

                expect(names(dir).size).to eq(2)
                expect(client).to have_received(:tide_data_for).twice
            end
        end

        it 'reuses the file while the key is unchanged' do
            with_test_cache_dir do |dir|
                station = build_station(id: 'TICON-1', provider: 'ticon')
                2.times { WebCalTides.tide_data_for(station) }

                expect(names(dir).size).to eq(1)
                expect(client).to have_received(:tide_data_for).once
            end
        end

        %w[noaa chs bsh kartverket linz imi rws].each do |provider|
            it "keeps the #{provider} file name as before and ignores the flag and dataset" do
                agency = double("#{provider} client")
                allow(agency).to receive(:tide_data_for) { [build_tide_data(time: DateTime.now)] }
                allow(WebCalTides).to receive(:tide_clients).with(provider).and_return(agency)

                with_test_cache_dir do |dir|
                    station = build_station(id: 'AG1', provider: provider)
                    WebCalTides.tide_data_for(station)
                    flip_flag
                    allow(WebCalTides).to receive(:harmonics_checksum).and_return('cccccccc_dddddddd')
                    WebCalTides.tide_data_for(station)

                    expect(names(dir)).to eq(["tides_v#{Models::TideData.version}_AG1_202506.json"])
                    expect(agency).to have_received(:tide_data_for).once
                end
            end
        end
    end

    describe 'currents JSON' do
        let(:client) { double('current client') }

        before do
            allow(client).to receive(:current_data_for) { [build_current_data(time: DateTime.now)] }
            allow(WebCalTides).to receive(:current_clients).and_call_original
            allow(WebCalTides).to receive(:current_clients).with('xtide').and_return(client)
        end

        it 'writes a new harmonics file when the engine flag flips, and does not serve the old one' do
            with_test_cache_dir do |dir|
                station = build_station(id: 'XC1', bid: 'XC1', provider: 'xtide')
                WebCalTides.current_data_for(station)
                first = names(dir)
                flip_flag
                WebCalTides.current_data_for(station)

                expect(first.size).to eq(1)
                expect(names(dir).size).to eq(2)
                expect(client).to have_received(:current_data_for).twice
            end
        end

        # An empty result (e.g. a station looked up while the station list was still loading) must not
        # be cached: it would serve an empty feed for the rest of the month.
        it 'does not cache an empty current prediction, and predicts again on the next request' do
            allow(client).to receive(:current_data_for).and_return([], [build_current_data(time: DateTime.now)])

            with_test_cache_dir do |dir|
                station = build_station(id: 'XC2', bid: 'XC2', provider: 'xtide')

                expect(WebCalTides.current_data_for(station)).to be_nil
                expect(names(dir, 'currents_*')).to be_empty

                expect(WebCalTides.current_data_for(station).size).to eq(1)
                expect(names(dir, 'currents_*').size).to eq(1)
                expect(client).to have_received(:current_data_for).twice
            end
        end

        it 'keeps the NOAA currents file name as before and ignores the flag and dataset' do
            noaa = double('noaa currents')
            allow(noaa).to receive(:current_data_for) { [build_current_data(time: DateTime.now)] }
            allow(WebCalTides).to receive(:current_clients).with('noaa').and_return(noaa)

            with_test_cache_dir do |dir|
                station = build_station(id: 'ACT1', bid: 'ACT1_1', provider: 'noaa')
                WebCalTides.current_data_for(station)
                flip_flag
                allow(WebCalTides).to receive(:harmonics_checksum).and_return('cccccccc_dddddddd')
                WebCalTides.current_data_for(station)

                expect(names(dir)).to eq(["currents_v#{Models::CurrentData.version}_ACT1_1_202506.json"])
                expect(noaa).to have_received(:current_data_for).once
            end
        end
    end

    describe 'GET /:type/:station.ics on a warm cache', type: :api do
        include Rack::Test::Methods

        let(:tide_client) { double('harmonics tides') }
        let(:current_client) { double('harmonics currents') }
        let(:heights) { [1.25, 7.75] }

        before do
            calls = 0
            allow(tide_client).to receive(:tide_data_for) do
                [build_tide_data(time: DateTime.now + 1, prediction: heights[(calls += 1) - 1] || 3.5)]
            end
            allow(current_client).to receive(:current_data_for) do
                [build_current_data(time: DateTime.now + 1, velocity_major: heights[(calls += 1) - 1] || 3.5)]
            end
            allow(WebCalTides).to receive(:tide_clients).and_call_original
            allow(WebCalTides).to receive(:tide_clients).with('ticon').and_return(tide_client)
            allow(WebCalTides).to receive(:tide_clients).with('noaa').and_return(tide_client)
            allow(WebCalTides).to receive(:current_clients).and_call_original
            allow(WebCalTides).to receive(:current_clients).with('xtide').and_return(current_client)
            allow(WebCalTides).to receive(:station_ids).and_return(%w[TICON-1 NOAA1 XC1])
            allow(WebCalTides).to receive(:tide_station_for).with('TICON-1').and_return(build_station(id: 'TICON-1', provider: 'ticon'))
            allow(WebCalTides).to receive(:tide_station_for).with('NOAA1').and_return(build_station(id: 'NOAA1', provider: 'noaa'))
            allow(WebCalTides).to receive(:current_station_for).with('XC1').and_return(build_station(id: 'XC1', bid: 'XC1', provider: 'xtide'))
            allow(WebCalTides).to receive(:cleanup_if_month_changed)
        end

        it 'writes a new tide ICS after the flag flips and serves the new predictions, not the old file' do
            with_test_cache_dir do |dir|
                get '/tides/TICON-1.ics?solar=0'
                expect(last_response).to be_ok
                first = names(dir, '*.ics')

                flip_flag
                get '/tides/TICON-1.ics?solar=0'

                expect(first.size).to eq(1)
                expect(names(dir, '*.ics').size).to eq(2)
                expect(last_response.body).to include('7.75')
            end
        end

        it 'writes a new currents ICS after the flag flips and serves the new predictions, not the old file' do
            with_test_cache_dir do |dir|
                get '/currents/XC1.ics?solar=0'
                expect(last_response).to be_ok
                first = names(dir, '*.ics')

                flip_flag
                get '/currents/XC1.ics?solar=0'

                expect(first.size).to eq(1)
                expect(names(dir, '*.ics').size).to eq(2)
                expect(last_response.body).to include('7.75')
            end
        end

        it 'keeps the agency ICS and JSON names as before and serves the cached file after a flip' do
            with_test_cache_dir do |dir|
                get '/tides/NOAA1.ics?solar=0'
                flip_flag
                allow(WebCalTides).to receive(:harmonics_checksum).and_return('cccccccc_dddddddd')
                get '/tides/NOAA1.ics?solar=0'

                expect(names(dir)).to eq(%w[tides_v2_NOAA1_202506.json tides_v2_NOAA1_202506_imperial_0_0.ics])
                expect(tide_client).to have_received(:tide_data_for).once
                expect(last_response.body).to include('1.25')
            end
        end

        it 'keeps "_YYYYMM" as the first cleanup token in every new name' do
            with_test_cache_dir do |dir|
                get '/tides/TICON-1.ics?solar=0&lunar=1'
                get '/tides/TICON-1.ics?units=metric'
                get '/currents/XC1.ics?solar=0'
                flip_flag
                get '/tides/TICON-1.ics?solar=0'
                get '/currents/XC1.ics?solar=0'

                rendered = names(dir).grep(/\A(tides|currents)_/)
                # tides and currents, JSON and ICS, both flag values, each with the dataset key
                expect(rendered.size).to eq(9)
                expect(rendered.grep(/_202506_#{checksum}_hA3(tcd|legacy)[_.]/)).to eq(rendered)
                rendered.each do |name|
                    expect(name[/_(20\d{4})[_.]/, 1]).to eq('202506'), "cleanup token of #{name}"
                end
            end
        end
    end

    describe 'monthly cleanup of the new names' do
        def write_month(month_time)
            Timecop.freeze(month_time)
            client = double('harmonics', tide_data_for: [build_tide_data(time: DateTime.now)],
                                         current_data_for: [build_current_data(time: DateTime.now)])
            allow(WebCalTides).to receive(:tide_clients).with('ticon').and_return(client)
            allow(WebCalTides).to receive(:current_clients).with('xtide').and_return(client)
            WebCalTides.tide_data_for(build_station(id: 'TICON-1', provider: 'ticon'))
            WebCalTides.current_data_for(build_station(id: 'XC1', bid: 'XC1', provider: 'xtide'))
        end

        it 'deletes previous-month files in the new style and keeps the current month' do
            with_test_cache_dir do |dir|
                write_month(Time.utc(2025, 5, 20))
                may = names(dir)
                # ICS names are built in server.rb; render them the same way for both months
                may_ics = may.map { |n| n.sub(/\.json\z/, '_imperial_1_0.ics') }
                may_ics.each { |n| File.write("#{dir}/#{n}", 'x') }

                write_month(june)
                june_names = names(dir) - may - may_ics
                june_names.each { |n| File.write("#{dir}/#{n.sub(/\.json\z/, '_metric_0_1.ics')}", 'x') }
                kept = names(dir) - may - may_ics

                expect(may.size).to eq(2)
                expect(may.grep(/hA3tcd/).size).to eq(2)
                WebCalTides.cleanup_old_cache_files

                expect(names(dir)).to eq(kept)
                expect(kept.size).to eq(4)
            end
        end
    end
end

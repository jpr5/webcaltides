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
        # TICON ids (T + 7 hex digits) that are not in the shipped data/ticon.json.  Other ids (XTide,
        # an agency, a current station) are not in that file; the startup check covers them.
        def unshipped_ticon_ids(ids)
            shipped = JSON.parse(File.read(File.expand_path('../../data/ticon.json', __dir__)))['stations'].map { |s| s['id'] }.to_set
            ids.select { |id| id.match?(/\AT\h{7}\z/) && !shipped.include?(id) }
        end

        it 'names only TICON stations that are in the shipped data/ticon.json, as demoted or better' do
            ids = WebCalTides::DEMOTED_STATIONS.flat_map { |id, entry| [id, entry[:better]] }
            expect(unshipped_ticon_ids(ids)).to be_empty
        end

        it 'accepts XTide, agency and current-station ids, and rejects a TICON id that is not shipped' do
            expect(unshipped_ticon_ids(%w[T6e11ade Xe1d6ce3 8443970 ACT1234_10 Tdeadbee])).to eq(['Tdeadbee'])
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

    # Server.warm_caches, as at boot, on the shipped data
    describe 'startup check' do
        let(:current) do
            build_station(name: 'Test Current', id: 'C0000001', bid: 'C0000001_10', public_id: 'C0000001_10',
                          provider: 'xtide', depth: 10)
        end
        let(:current2) do
            build_station(name: 'Test Current 2', id: 'C0000002', bid: 'C0000002', public_id: 'C0000002', provider: 'xtide')
        end

        before do
            allow(WebCalTides).to receive(:get_harmonics_client).and_return(@client)
            allow(WebCalTides).to receive(:current_stations).and_return([current, current2])
            allow(WebCalTides).to receive(:cleanup_old_cache_files)
            allow($LOG).to receive(:error)
            allow($LOG).to receive(:warn)
        end

        def warm_caches
            Server.warm_caches.join
        end

        def entry(better, type = :tide)
            { type: type, better: better, warning: 'w', evidence: 'e' }
        end

        it 'logs an error for each demoted or better id that is not loaded, and none for the shipped list' do
            stub_const('WebCalTides::DEMOTED_STATIONS', {
                'T6e11ade' => entry('Tdce40e9'), 'Tgone000' => entry('Tdce40e9'), 'Tdce40e9' => entry('Tlost000')
            })
            warm_caches

            expect($LOG).to have_received(:error).with(/!! demoted station Tgone000 is not in the tide station list loaded at startup \(\d+ stations\)/).once
            expect($LOG).to have_received(:error).with(/!! better station Tlost000 for demoted station Tdce40e9 is not in the tide station list loaded at startup/).once
            expect($LOG).not_to have_received(:error).with(/T6e11ade/)
            expect(WebCalTides).to have_received(:cleanup_old_cache_files)
        end

        it 'logs nothing for the shipped list' do
            warm_caches
            expect($LOG).not_to have_received(:error)
        end

        it 'matches a current station by its id, as demotion does, not by the id of one of its bins' do
            stub_const('WebCalTides::DEMOTED_STATIONS', {
                'C0000001' => entry('C0000002', :current), 'C0000001_10' => entry('C0000002', :current)
            })
            warm_caches

            expect(WebCalTides.demoted_station?(current)).to be(true)
            expect($LOG).to have_received(:error).with(/!! demoted station C0000001_10 is not in the current station list/).once
            expect($LOG).not_to have_received(:error).with(/!! demoted station C0000001 /)
            expect($LOG).not_to have_received(:error).with(/C0000002/)
        end

        it 'looks for each station in the list of its own type' do
            stub_const('WebCalTides::DEMOTED_STATIONS', { 'T6e11ade' => entry('C0000002'), 'C0000001' => entry('Tdce40e9', :current) })
            warm_caches

            expect($LOG).to have_received(:error).with(/!! better station C0000002 for demoted station T6e11ade is not in the tide station list/).once
            expect($LOG).to have_received(:error).with(/!! better station Tdce40e9 for demoted station C0000001 is not in the current station list/).once
        end

        it 'still cleans the cache when the check fails, and logs where it failed' do
            allow(WebCalTides).to receive(:check_demoted_stations) { raise RuntimeError, 'boom' }
            warm_caches

            expect(WebCalTides).to have_received(:cleanup_old_cache_files)
            expect($LOG).to have_received(:error).with(/demoted station check failed: RuntimeError - boom/)
            expect($LOG).to have_received(:error).with(%r{demoted_stations_spec\.rb:\d+})
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

            # Visible without JavaScript: not hidden, and its text is in the element itself
            warning = by_id['T6e11ade'][/<p[^>]*demotion-warning[^>]*>.*?<\/p>/m]
            expect(warning).not_to include('display: none')
            expect(warning[/<span[^>]*>(.*?)<\/span>/m, 1]).to eq('Times at this station may be about 1 h early.  Use Vigo, ESP (Tdce40e9) instead.')
            expect(by_id['Tdce40e9']).not_to include('1 h early')
            expect(by_id['T0000001']).not_to include('1 h early')
        end

        # The warning follows the source picked in the card (Alpine's currentSource), so it shows when
        # the demoted station is picked as an alternative
        it 'binds the card warning to the selected source' do
            post '/', { searchtext: 'vigo', units: 'metric' }
            card = last_response.body.split('class="station-card ').drop(1).find { |c| c.include?('data-station-id="Tdce40e9"') }
            warning = card[/<p[^>]*demotion-warning[^>]*>.*?<\/p>/m]

            expect(warning).to include('x-show="currentSource?.warning"', 'x-text="currentSource?.warning"')
            expect(warning).to include('style="display: none"')
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
        # Each example has its own cache, so none reads a feed another one wrote
        let(:cache_dir) { Dir.mktmpdir }
        after { FileUtils.rm_rf(cache_dir) }

        # A calendar's DESCRIPTION is a list of values
        def text(desc)
            Array(desc).map(&:to_s).join("\n\n")
        end

        def feed(id)
            get "/tides/#{id}.ics?solar=0"
            expect(last_response.status).to eq(200)
            Icalendar::Calendar.parse(last_response.body).first
        end

        before do
            Timecop.freeze(Time.utc(2026, 11, 15, 12))
            allow(Server.settings).to receive(:cache_dir).and_return(cache_dir)
            allow($LOG).to receive(:info)
            allow(WebCalTides).to receive(:cleanup_if_month_changed)
            allow(WebCalTides).to receive(:tide_clients).and_wrap_original do |m, *args|
                args.first.to_s.in?(%w[xtide ticon]) ? @client : m.call(*args)
            end
        end

        it 'serves T6e11ade with the warning on the calendar (DESCRIPTION and X-WR-CALDESC) and on every tide event' do
            cal = feed('T6e11ade')
            expect(cal.events).not_to be_empty
            expect(text(cal.description)).to include('about 1 h early', 'Tdce40e9', 'NOT FOR NAVIGATION')
            expect(text(cal.description)).not_to include('["')
            expect(cal.x_wr_caldesc.map(&:to_s)).to contain_exactly(include('about 1 h early', 'Tdce40e9'))
            expect(cal.events.map { |e| e.description.to_s }).to all(include('about 1 h early'))
        end

        it 'logs each time it serves the feed, from the cache too' do
            2.times { feed('T6e11ade') }
            expect(Dir["#{cache_dir}/*T6e11ade*.ics"].length).to eq(1)
            expect($LOG).to have_received(:info).with(/serving demoted station T6e11ade/).twice
        end

        it 'serves the new warning, not a cached feed, when the entry changes' do
            feed('T6e11ade')
            stub_const('WebCalTides::DEMOTED_STATIONS', {
                'T6e11ade' => WebCalTides::DEMOTED_STATIONS['T6e11ade'].merge(warning: 'Times here are under review')
            })
            cal = feed('T6e11ade')
            expect(text(cal.description)).to include('Times here are under review')
            expect(text(cal.description)).not_to include('about 1 h early')
        end

        it 'does not serve a feed cached before the demotion' do
            stale = "#{cache_dir}/tides_v#{Models::TideData.version}_T6e11ade_202611" \
                    "#{WebCalTides.harmonics_cache_key(WebCalTides.tide_station_for('T6e11ade'))}_imperial_0_0.ics"
            File.write(stale, "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:stale\r\nEND:VCALENDAR\r\n")

            get '/tides/T6e11ade.ics?solar=0'
            expect(last_response.status).to eq(200)
            expect(last_response.body).not_to include('PRODID:stale')
            expect(last_response.body).to include('about 1 h early')
        end

        it 'puts the warning on tide events only, not on sunrise and sunset' do
            get '/tides/T6e11ade.ics' # solar events are on by default
            expect(last_response.status).to eq(200)
            events = Icalendar::Calendar.parse(last_response.body).first.events
            tides, others = events.partition { |e| e.summary.to_s.include?('Tide') }

            expect(tides).not_to be_empty
            expect(others.map { |e| e.summary.to_s }).to include(match(/sunrise/i), match(/sunset/i))
            expect(tides.map { |e| e.description.to_s }).to all(include('about 1 h early'))
            expect(others.map { |e| e.description.to_s }).to all(satisfy { |d| !d.include?('1 h early') })
        end

        it 'serves Tdce40e9 without the warning' do
            feed('Tdce40e9')
            expect(last_response.body).not_to include('1 h early')
        end

        it 'puts the warning in an existing X-WR-CALDESC, once' do
            cal = Icalendar::Calendar.new
            cal.description = 'Source credit'
            cal.append_custom_property('X-WR-CALDESC', 'Source credit')
            cal.event { |e| e.summary = 'High Tide' }

            WebCalTides.add_demotion_warning(cal, WebCalTides.tide_station_for('T6e11ade'))

            expect(cal.custom_property('X-WR-CALDESC').map(&:to_s)).to eq([text(cal.description)])
            expect(cal.to_ical.scan(/^X-WR-CALDESC/).length).to eq(1)
            expect(text(cal.description)).to eq("Times at this station may be about 1 h early.  Use Vigo, ESP (Tdce40e9) instead.\n\nSource credit")
        end
    end

    # The better station is looked up in a table built once per station list of the demoted
    # station's type.  These use fixed lists, with the app's own @tide_stations where a station is
    # removed from it in place.
    describe 'warning' do
        let(:demoted) { @harmonics_stations.find { |s| s.id == 'T6e11ade' } }
        let(:list)    { @harmonics_stations.dup }

        around do |example|
            saved = %i[@tide_stations @tide_stations_retry_at @demotion_better_stations].to_h { |v| [v, WebCalTides.instance_variable_get(v)] }
            example.run
        ensure
            saved.each { |v, value| WebCalTides.instance_variable_set(v, value) }
        end

        before do
            WebCalTides.instance_variable_set(:@tide_stations, list)
            WebCalTides.instance_variable_set(:@tide_stations_retry_at, nil)
            WebCalTides.instance_variable_set(:@demotion_better_stations, nil)
            allow(WebCalTides).to receive(:tide_stations).and_return(list)
            allow(WebCalTides).to receive(:cache_tide_stations)
            allow($LOG).to receive(:error)
        end

        it 'reads the station list once, not on each call' do
            allow(list).to receive(:each).and_call_original
            3.times { expect(WebCalTides.demotion_warning(demoted)).to include('Vigo, ESP (Tdce40e9)') }
            expect(list).to have_received(:each).once
        end

        it 'builds the table again when the list is replaced' do
            WebCalTides.demotion_warning(demoted)
            renamed = list.map { |s| s.id == 'Tdce40e9' ? s.dup.tap { |r| r.name = 'Vigo IEO, ESP' } : s }
            allow(WebCalTides).to receive(:tide_stations).and_return(renamed)

            expect(WebCalTides.demotion_warning(demoted)).to include('Vigo IEO, ESP (Tdce40e9)')
        end

        it 'stops naming the better station once it is removed from the list in place' do
            expect(WebCalTides.demotion_warning(demoted)).to include('Vigo, ESP (Tdce40e9)')
            WebCalTides.remove_tide_station('Tdce40e9')

            expect(WebCalTides.demotion_warning(demoted)).to end_with('Use Tdce40e9 instead.')
        end

        it 'logs a missing better station once per list' do
            list.reject! { |s| s.id == 'Tdce40e9' }
            3.times { WebCalTides.demotion_warning(demoted) }
            expect($LOG).to have_received(:error).with(/!! better station Tdce40e9 for demoted station T6e11ade is not in the tide station list/).once

            allow(WebCalTides).to receive(:tide_stations).and_return(list.dup)
            WebCalTides.demotion_warning(demoted)
            expect($LOG).to have_received(:error).with(/!! better station Tdce40e9/).twice
        end

        it 'does not load the current station list for a tide station' do
            expect(WebCalTides).not_to receive(:current_stations)
            expect(WebCalTides.demotion_warning(demoted)).to include('Tdce40e9')
            expect(WebCalTides.demotion_cache_key(demoted)).to start_with('_demoted')
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

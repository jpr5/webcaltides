# frozen_string_literal: true

RSpec.describe 'POST /', type: :api do
    def stub_empty_search_results
        allow(WebCalTides).to receive(:find_tide_stations).and_return([])
        allow(WebCalTides).to receive(:find_current_stations).and_return([])
        allow(WebCalTides).to receive(:find_tide_stations_by_gps).and_return([])
        allow(WebCalTides).to receive(:find_current_stations_by_gps).and_return([])
        allow(WebCalTides).to receive(:group_search_results).and_return([])
    end

    before do
        stub_empty_search_results
    end

    describe 'empty search' do
        it 'returns 200 with index page when searchtext is empty' do
            post '/', searchtext: ''
            expect(last_response.status).to eq(200)
        end

        it 'returns 200 with index page when searchtext is nil' do
            post '/', {}
            expect(last_response.status).to eq(200)
        end

        it 'returns 200 with index page when searchtext is only whitespace' do
            post '/', searchtext: '   '
            expect(last_response.status).to eq(200)
        end

        it 'does not call search methods for empty input' do
            post '/', searchtext: ''
            expect(WebCalTides).not_to have_received(:find_tide_stations)
            expect(WebCalTides).not_to have_received(:find_current_stations)
        end
    end

    describe 'text search' do
        it 'returns 200 for a text search term' do
            post '/', searchtext: 'boston'
            expect(last_response.status).to eq(200)
        end

        it 'calls find_tide_stations and find_current_stations with tokenized search terms' do
            post '/', searchtext: 'boston'
            expect(WebCalTides).to have_received(:find_tide_stations).with(
                hash_including(by: ['boston'])
            )
            expect(WebCalTides).to have_received(:find_current_stations).with(
                hash_including(by: ['boston'])
            )
        end

        it 'tokenizes multi-word search terms' do
            post '/', searchtext: 'boston harbor'
            expect(WebCalTides).to have_received(:find_tide_stations).with(
                hash_including(by: ['boston', 'harbor'])
            )
        end

        it 'calls group_search_results separately for tides (no match_depth) and currents (match_depth: false)' do
            post '/', searchtext: 'boston'
            # Exact kwargs (not hash_including) so this also asserts the tides
            # call site passes NO match_depth key (see server.rb search handler)
            expect(WebCalTides).to have_received(:group_search_results).with(
                anything, compute_deltas: false
            ).once
            expect(WebCalTides).to have_received(:group_search_results).with(
                anything, hash_including(match_depth: false)
            ).once
        end
    end

    describe 'GPS search' do
        before do
            allow(WebCalTides).to receive(:parse_gps).and_return([42.3601, -71.0589])
        end

        it 'returns 200 for GPS coordinates' do
            post '/', searchtext: '42.3601, -71.0589'
            expect(last_response.status).to eq(200)
        end

        it 'calls parse_gps and GPS search methods' do
            post '/', searchtext: '42.3601, -71.0589'
            expect(WebCalTides).to have_received(:parse_gps)
            expect(WebCalTides).to have_received(:find_tide_stations_by_gps).with(
                42.3601, -71.0589, hash_including(within: 10, units: 'mi')
            )
            expect(WebCalTides).to have_received(:find_current_stations_by_gps).with(
                42.3601, -71.0589, hash_including(within: 10, units: 'mi')
            )
        end

        it 'does not call text search methods for GPS input' do
            post '/', searchtext: '42.3601, -71.0589'
            expect(WebCalTides).not_to have_received(:find_tide_stations)
            expect(WebCalTides).not_to have_received(:find_current_stations)
        end
    end

    describe 'units parameter' do
        it 'passes metric units when units=metric' do
            post '/', searchtext: 'boston', units: 'metric'
            expect(WebCalTides).to have_received(:find_tide_stations).with(
                hash_including(units: 'km')
            )
        end

        it 'passes imperial units when units=imperial' do
            post '/', searchtext: 'boston', units: 'imperial'
            expect(WebCalTides).to have_received(:find_tide_stations).with(
                hash_including(units: 'mi')
            )
            expect(WebCalTides).to have_received(:find_current_stations).with(
                hash_including(units: 'mi')
            )
        end

        it 'defaults to imperial units when units param is absent' do
            post '/', searchtext: 'boston'
            expect(WebCalTides).to have_received(:find_tide_stations).with(
                hash_including(units: 'mi')
            )
            expect(WebCalTides).to have_received(:find_current_stations).with(
                hash_including(units: 'mi')
            )
        end
    end

    describe 'within parameter' do
        it 'passes radius from within param' do
            post '/', searchtext: 'boston', within: '25'
            expect(WebCalTides).to have_received(:find_tide_stations).with(
                hash_including(within: 25)
            )
        end

        it 'defaults radius to 0 when within param is absent' do
            post '/', searchtext: 'boston'
            expect(WebCalTides).to have_received(:find_tide_stations).with(
                hash_including(within: 0)
            )
        end
    end

    describe 'XSS prevention' do
        it 'escapes script tags in the searchtext tokens echoed in the results summary' do
            post '/', searchtext: '<script>alert(1)</script>'
            expect(last_response.status).to eq(200)
            expect(last_response.body).not_to include('<script>alert(1)</script>')
            # Anchor on the results-summary markup so the escaped echo in the
            # search-form placeholder attribute cannot satisfy this assertion
            expect(last_response.body).to include(
                'Showing results for "<span class="text-white font-medium">&lt;script&gt;alert(1)&lt;/script&gt;</span>"'
            )
        end

        it 'encodes webcal/https base URLs so a malicious Host header cannot break out of x-data' do
            station = build_station
            group = WebCalTides::StationGroup.new(primary: station, alternatives: [], deltas: nil)
            # Key the stub on kwargs rather than call order: the currents call
            # site passes match_depth:, the tides call site does not (server.rb)
            allow(WebCalTides).to receive(:group_search_results) do |*_args, **kwargs|
                kwargs.key?(:match_depth) ? [] : [group]
            end

            header 'Host', "evil';alert(1);x"
            post '/', searchtext: 'boston'

            expect(last_response.status).to eq(200)
            expect(last_response.body).not_to include("webcal://evil';alert(1);x")
            expect(last_response.body).to include('webcalBase: &quot;webcal://evil&#39;;alert(1);x/tides/&quot;')
            # https_base is built from the same Host header on a separate line in
            # views/index.erb, so pin its encoding independently of webcal_base
            # (scheme is http:// under rack-test)
            expect(last_response.body).not_to include("http://evil';alert(1);x")
            expect(last_response.body).to include('httpsBase: &quot;http://evil&#39;;alert(1);x/tides/&quot;')
        end

        # Body assertions guard the composite regression: a raw units passthrough
        # to the view local plus a future view echoing it.
        it 'normalizes a script-tag units parameter to the imperial default and keeps it off the page' do
            post '/', searchtext: 'boston', units: '<script>alert(1)</script>'
            expect(last_response.status).to eq(200)
            expect(WebCalTides).to have_received(:find_tide_stations).with(
                hash_including(units: 'mi')
            )
            expect(last_response.body).not_to include('<script>alert(1)</script>')
        end

        it 'normalizes an attribute-breakout units parameter to the imperial default and keeps it off the page' do
            post '/', searchtext: 'boston', units: '"><img src=x>'
            expect(last_response.status).to eq(200)
            expect(WebCalTides).to have_received(:find_tide_stations).with(
                hash_including(units: 'mi')
            )
            expect(last_response.body).not_to include('"><img src=x>')
        end

        it 'escapes the placeholder value derived from searchtext' do
            # The placeholder local is escaped via Rack::Utils.escape_html (server.rb)
            post '/', searchtext: '"><script>alert(1)</script>'
            expect(last_response.status).to eq(200)
            expect(last_response.body).not_to include('"><script>alert(1)</script>')
            expect(last_response.body).to include('placeholder="&quot;&gt;&lt;script&gt;')
        end

        it 'returns 200 for benign search terms and renders the results summary' do
            post '/', searchtext: 'boston', units: 'imperial'
            expect(last_response.status).to eq(200)
            # Anchor on the summary span markup (views/index.erb) so the search
            # text echoed into the form placeholder attribute cannot satisfy
            # this assertion when the summary renders nothing
            expect(last_response.body).to include(
                'Showing results for "<span class="text-white font-medium">boston</span>"'
            )
        end
    end

    describe 'invalid GPS input' do
        it 'returns gracefully when GPS parsing fails' do
            allow(WebCalTides).to receive(:parse_gps).and_return(nil)
            post '/', searchtext: '999.999, 999.999'
            expect(last_response.status).to eq(200)
        end

        it 'does not call GPS search methods when parsing fails' do
            allow(WebCalTides).to receive(:parse_gps).and_return(nil)
            post '/', searchtext: '999.999, 999.999'
            expect(WebCalTides).not_to have_received(:find_tide_stations_by_gps)
            expect(WebCalTides).not_to have_received(:find_current_stations_by_gps)
        end
    end
end

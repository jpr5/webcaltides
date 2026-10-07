# frozen_string_literal: true

require 'tempfile'
require 'stringio'
require 'open3'
require 'rbconfig'
require_relative '../../scripts/check_harmonics_releases'

RSpec.describe HarmonicsReleaseCheck do
    let(:xtide_url) { HarmonicsReleaseCheck::XTIDE_URL }
    let(:ticon_url) { HarmonicsReleaseCheck::TICON_URL }

    def xtide_listing(*dates, ext: 'tar.xz')
        links = dates.map do |d|
            file = d.include?('.') ? "harmonics-dwf-#{d.sub('.', '-free.')}" : "harmonics-dwf-#{d}-free.#{ext}"
            %(<a href="#{file}">#{file}</a>)
        end
        "<html><body><pre>#{links.join("\n")}\n<a href=\"xtide-2.15.5.tar.xz\">xtide</a></pre></body></html>"
    end

    # Rows shaped like the Apache index at flaterco.com/files/xtide/.
    def apache_listing(*files)
        rows = files.map do |f|
            %(<tr><td valign="top"><img src="/apache_icons/unknown.gif" alt="[   ]"></td>) +
                %(<td><a href="#{f}">#{f}</a></td><td align="right">2026-12-30 17:20  </td></tr>)
        end
        "<html><body><table>#{rows.join("\n")}</table></body></html>"
    end

    def ticon_response(*records)
        data = records.map do |title, doi|
            { 'id' => doi, 'attributes' => { 'doi' => doi, 'titles' => [{ 'title' => title }] } }
        end
        { 'data' => data }.to_json
    end

    let(:ticon_noise) do
        [
            ['Ticonderoga historical survey', '10.1234/noise1'],
            ['TICON tidal analysis notes', '10.1234/noise2'],
            ['A study citing TICON-9: not a release', '10.1234/noise3'],
            ['TICON: Tidal Constants', '10.1234/noise4'],
            ['Ticon', '10.1234/noise5'],
            ['TICON-td: Third-degree tidal constants', '10.1234/noise6'],
            ['TICON: A Slide-Level Tile Contextualizer for Histopathology', '10.1234/noise7']
        ]
    end

    describe '.check_xtide' do
        it 'reports a newer release when the listing has a later date than the baseline' do
            stub_request(:get, xtide_url).to_return(status: 200, body: xtide_listing('20241229', '20251228', '20261230'))
            result = described_class.check_xtide(baseline: '20251228')
            expect(result.status).to eq(:new)
            expect(result.message).to include('20261230')
            expect(result.message).to include('harmonics-dwf-20261230-free.tar.xz')
        end

        it 'passes when the newest date equals the baseline' do
            stub_request(:get, xtide_url).to_return(status: 200, body: xtide_listing('20241229', '20251228'))
            result = described_class.check_xtide(baseline: '20251228')
            expect(result.status).to eq(:ok)
            expect(result.message).to include('20251228')
        end

        it 'reports a newer release published in a different archive format' do
            stub_request(:get, xtide_url).to_return(status: 200,
                                                    body: xtide_listing('20241229', '20251228', '20261230.tar.zst'))
            result = described_class.check_xtide(baseline: '20251228')
            expect(result.status).to eq(:new)
            expect(result.message).to include('harmonics-dwf-20261230-free.tar.zst')
            expect(result.message).not_to include('20261230-free.tar.xz')
        end

        it 'still reads the older .tar.bz2 releases' do
            stub_request(:get, xtide_url).to_return(status: 200, body: xtide_listing('20180101', '20190620', ext: 'tar.bz2'))
            result = described_class.check_xtide(baseline: '20180101')
            expect(result.status).to eq(:new)
            expect(result.message).to include('harmonics-dwf-20190620-free.tar.bz2')
        end

        it 'finds the newest date when the listing is not in date order' do
            stub_request(:get, xtide_url).to_return(status: 200, body: xtide_listing('20261230', '20241229', '20251228'))
            result = described_class.check_xtide(baseline: '20251228')
            expect(result.status).to eq(:new)
            expect(result.message).to include('harmonics-dwf-20261230-free.tar.xz')
        end

        it 'names the archive, not a signature or checksum sidecar, when they share the newest date' do
            listing = xtide_listing('20241229', '20251228.tar.xz.sig', '20251228.tar.xz.sha256', '20251228.tar.xz')
            stub_request(:get, xtide_url).to_return(status: 200, body: listing)
            result = described_class.check_xtide(baseline: '20241229')
            expect(result.status).to eq(:new)
            expect(result.message).to include('harmonics-dwf-20251228-free.tar.xz ')
            expect(result.message).not_to match(/\.(sig|sha256|md5|asc)\b/)
        end

        it 'still detects a newer date that so far has only a sidecar file' do
            stub_request(:get, xtide_url).to_return(status: 200, body: xtide_listing('20251228', '20261230.tar.xz.sig'))
            result = described_class.check_xtide(baseline: '20251228')
            expect(result.status).to eq(:new)
            expect(result.message).to include('20261230')
        end

        it 'reports a newer release that so far has only the SQL variant' do
            listing = apache_listing('harmonics-dwf-20251228-SQL.tar.xz', 'harmonics-dwf-20251228-free.tar.xz',
                                     'harmonics-dwf-20261230-SQL.tar.xz')
            stub_request(:get, xtide_url).to_return(status: 200, body: listing)
            result = described_class.check_xtide(baseline: '20251228')
            expect(result.status).to eq(:new)
            expect(result.message).to include('harmonics-dwf-20261230-SQL.tar.xz')
        end

        it 'reports a newer release when the free variant is renamed' do
            listing = apache_listing('harmonics-dwf-20251228-free.tar.xz', 'harmonics-dwf-20261230-public.tar.xz')
            stub_request(:get, xtide_url).to_return(status: 200, body: listing)
            result = described_class.check_xtide(baseline: '20251228')
            expect(result.status).to eq(:new)
            expect(result.message).to include('harmonics-dwf-20261230-public.tar.xz')
        end

        it 'names the free archive when the free and SQL variants share the newest date' do
            listing = apache_listing('harmonics-dwf-20261230-SQL.tar.xz', 'harmonics-dwf-20261230-free.tar.xz.sig',
                                     'harmonics-dwf-20261230-free.tar.xz')
            stub_request(:get, xtide_url).to_return(status: 200, body: listing)
            result = described_class.check_xtide(baseline: '20251228')
            expect(result.status).to eq(:new)
            expect(result.message).to include('harmonics-dwf-20261230-free.tar.xz ')
            expect(result.message).to end_with("#{xtide_url}harmonics-dwf-20261230-free.tar.xz")
        end

        it 'names a non-sidecar archive over a free sidecar when there is no free archive' do
            listing = apache_listing('harmonics-dwf-20261230-free.tar.xz.sig', 'harmonics-dwf-20261230-SQL.tar.xz')
            stub_request(:get, xtide_url).to_return(status: 200, body: listing)
            result = described_class.check_xtide(baseline: '20251228')
            expect(result.status).to eq(:new)
            expect(result.message).to end_with("#{xtide_url}harmonics-dwf-20261230-SQL.tar.xz")
        end

        it 'fails with a check error when the listing has no harmonics files' do
            stub_request(:get, xtide_url).to_return(status: 200, body: '<html>maintenance</html>')
            result = described_class.check_xtide(baseline: '20251228')
            expect(result.status).to eq(:error)
        end

        it 'retries once, then fails with a check error when the fetch keeps failing' do
            stub_request(:get, xtide_url).to_return(status: 503, body: 'down')
            result = described_class.check_xtide(baseline: '20251228')
            expect(result.status).to eq(:error)
            expect(a_request(:get, xtide_url)).to have_been_made.twice
            expect(result.message).to include('CheckError: HTTP 503')
            expect(result.message.scan('CheckError').size).to eq(1)
        end

        it 'succeeds when the retry succeeds' do
            stub_request(:get, xtide_url)
                .to_return({ status: 500, body: '' }, { status: 200, body: xtide_listing('20251228') })
            expect(described_class.check_xtide(baseline: '20251228').status).to eq(:ok)
        end
    end

    describe '.check_ticon' do
        it 'reports a newer release for a TICON-5 title' do
            stub_request(:get, ticon_url).to_return(status: 200, body: ticon_response(
                ['TICON-4: TIdal CONstants based on GESLA-4', '10.17882/109129'],
                ['TICON-5: TIdal CONstants based on GESLA-5', '10.17882/999999'],
                *ticon_noise
            ))
            result = described_class.check_ticon(baseline: 4)
            expect(result.status).to eq(:new)
            expect(result.message).to include('TICON-5')
            expect(result.message).to include('https://doi.org/10.17882/999999')
        end

        it 'passes when TICON-4 is the newest release, ignoring noisy titles' do
            stub_request(:get, ticon_url).to_return(status: 200, body: ticon_response(
                ['TICON-3: TIdal CONstants based on GESLA-3', '10.1594/PANGAEA.951610'],
                ['TICON-4: TIdal CONstants based on GESLA-4', '10.17882/109129'],
                *ticon_noise
            ))
            result = described_class.check_ticon(baseline: 4)
            expect(result.status).to eq(:ok)
            expect(result.message).to include('TICON-4')
        end

        it 'finds the newest release when DataCite lists it first' do
            stub_request(:get, ticon_url).to_return(status: 200, body: ticon_response(
                ['TICON-5: TIdal CONstants based on GESLA-5', '10.17882/999999'],
                ['TICON-4: TIdal CONstants based on GESLA-4', '10.17882/109129'],
                ['TICON-3: TIdal CONstants based on GESLA-3', '10.1594/PANGAEA.951610'],
                *ticon_noise
            ))
            result = described_class.check_ticon(baseline: 4)
            expect(result.status).to eq(:new)
            expect(result.message).to include('TICON-5')
            expect(result.message).to include('https://doi.org/10.17882/999999')
        end

        [
            'TICON-5 - TIdal CONstants based on GESLA-5',
            'TICON-5 : TIdal CONstants based on GESLA-5',
            'TICON-5 (GESLA-5)',
            'ticon-5: tidal constants',
            'TICON 5: TIdal CONstants based on GESLA-5',
            'TICON5 TIdal CONstants based on GESLA-5',
            "TICON\u20135: TIdal CONstants based on GESLA-5",
            "TICON\u20145: TIdal CONstants based on GESLA-5",
            'TICON-12: TIdal CONstants based on GESLA-12'
        ].each do |title|
            it "reports a newer release for the title #{title.inspect}" do
                stub_request(:get, ticon_url).to_return(status: 200, body: ticon_response(
                    ['TICON-4: TIdal CONstants based on GESLA-4', '10.17882/109129'],
                    [title, '10.17882/999999'],
                    *ticon_noise
                ))
                result = described_class.check_ticon(baseline: 4)
                expect(result.status).to eq(:new)
                expect(result.message).to match(/TICON-(5|12) /)
            end
        end

        ['TICON 2025 workshop proceedings', 'TICON-2025: annual report', 'TICON 123 notes'].each do |title|
            it "does not read a year or long number in #{title.inspect} as a release" do
                stub_request(:get, ticon_url).to_return(status: 200, body: ticon_response(
                    ['TICON-4: TIdal CONstants based on GESLA-4', '10.17882/109129'],
                    [title, '10.1234/noise-year']
                ))
                result = described_class.check_ticon(baseline: 4)
                expect(result.status).to eq(:ok)
                expect(result.message).to include('latest TICON-4')
            end
        end

        it 'fails with a check error when no TICON-N titles match' do
            stub_request(:get, ticon_url).to_return(status: 200, body: ticon_response(*ticon_noise))
            expect(described_class.check_ticon(baseline: 4).status).to eq(:error)
        end

        it 'fails with a check error when DataCite reports more records than it returned' do
            body = JSON.parse(ticon_response(['TICON-4: TIdal CONstants based on GESLA-4', '10.17882/109129']))
            body['meta'] = { 'total' => 1500 }
            stub_request(:get, ticon_url).to_return(status: 200, body: body.to_json)
            result = described_class.check_ticon(baseline: 4)
            expect(result.status).to eq(:error)
            expect(result.message).to match(/1 of 1500/)
        end

        it 'passes when DataCite meta.total matches the records returned' do
            body = JSON.parse(ticon_response(['TICON-4: TIdal CONstants based on GESLA-4', '10.17882/109129']))
            body['meta'] = { 'total' => 1 }
            stub_request(:get, ticon_url).to_return(status: 200, body: body.to_json)
            expect(described_class.check_ticon(baseline: 4).status).to eq(:ok)
        end

        it 'fails with a check error when the response is not JSON' do
            stub_request(:get, ticon_url).to_return(status: 200, body: '<html>oops</html>')
            expect(described_class.check_ticon(baseline: 4).status).to eq(:error)
        end
    end

    describe '.run' do
        let(:summary) { Tempfile.new('step-summary') }
        after { summary.close! }

        before do
            stub_request(:get, xtide_url).to_return(status: 200, body: xtide_listing('20251228'))
            stub_request(:get, ticon_url).to_return(status: 200, body: ticon_response(
                ['TICON-4: TIdal CONstants based on GESLA-4', '10.17882/109129']
            ))
        end

        it 'returns 0 and writes one line per source when nothing is new' do
            out = StringIO.new
            code = described_class.run(env: { 'GITHUB_STEP_SUMMARY' => summary.path }, out: out)
            expect(code).to eq(0)
            expect(out.string.lines.grep(/XTide|TICON/).size).to eq(2)
            expect(File.read(summary.path)).to include('XTide').and include('TICON')
        end

        it 'returns the new-release code when XTIDE_BASELINE is older than the latest release' do
            out = StringIO.new
            code = described_class.run(env: { 'XTIDE_BASELINE' => '20241229' }, out: out)
            expect(code).to eq(HarmonicsReleaseCheck::EXIT_NEW_RELEASE)
            expect(out.string).to include('20251228')
        end

        it 'returns the new-release code when the newest file has no variant in its name' do
            stub_request(:get, xtide_url).to_return(status: 200, body: apache_listing(
                'harmonics-dwf-20251228-free.tar.xz', 'harmonics-dwf-20261230.tar.xz'
            ))
            out = StringIO.new
            code = described_class.run(env: {}, out: out)
            expect(code).to eq(HarmonicsReleaseCheck::EXIT_NEW_RELEASE)
            expect(out.string).to include("#{xtide_url}harmonics-dwf-20261230.tar.xz")
        end

        it 'returns the new-release code when the newest file has an extra name part after the variant' do
            stub_request(:get, xtide_url).to_return(status: 200, body: apache_listing(
                'harmonics-dwf-20251228-free.tar.xz', 'harmonics-dwf-20261230-SQL-v2.tar.xz',
                'harmonics-dwf-20261230-free-v2.tar.xz'
            ))
            out = StringIO.new
            code = described_class.run(env: {}, out: out)
            expect(code).to eq(HarmonicsReleaseCheck::EXIT_NEW_RELEASE)
            expect(out.string).to include("#{xtide_url}harmonics-dwf-20261230-free-v2.tar.xz\n")
        end

        it 'does not read a longer digit run after harmonics-dwf- as a date' do
            stub_request(:get, xtide_url).to_return(status: 200, body: apache_listing(
                'harmonics-dwf-20251228-free.tar.xz', 'harmonics-dwf-202612301.tar.xz'
            ))
            out = StringIO.new
            code = described_class.run(env: {}, out: out)
            expect(code).to eq(HarmonicsReleaseCheck::EXIT_OK)
            expect(out.string).to include('latest 20251228')
        end

        it 'returns a distinct non-zero code when a source cannot be checked' do
            stub_request(:get, ticon_url).to_return(status: 200, body: '{}')
            out = StringIO.new
            code = described_class.run(env: {}, out: out)
            expect(code).to eq(HarmonicsReleaseCheck::EXIT_CHECK_FAILED)
            expect(HarmonicsReleaseCheck::EXIT_CHECK_FAILED).not_to eq(HarmonicsReleaseCheck::EXIT_NEW_RELEASE)
            expect(out.string).to match(/check failed/i)
        end

        it 'reports the check as failed when fetch raises an unexpected error inside a check' do
            allow(described_class).to receive(:fetch).and_raise(NoMethodError, 'boom')
            out = StringIO.new
            code = described_class.run(env: {}, out: out)
            expect(code).to eq(HarmonicsReleaseCheck::EXIT_CHECK_FAILED)
            expect(out.string).to match(/check failed.*NoMethodError.*boom/i)
        end

        it 'returns the check-failed code when an error escapes to the top-level rescue' do
            allow(described_class).to receive(:check_ticon).and_raise(RuntimeError, 'escaped')
            out = StringIO.new
            err = StringIO.new
            code = described_class.run(env: {}, out: out, err: err)
            expect(code).to eq(HarmonicsReleaseCheck::EXIT_CHECK_FAILED)
            expect(err.string).to include('CHECK FAILED: RuntimeError: escaped')
            expect(out.string).to be_empty
        end

        it 'treats an empty XTIDE_BASELINE as unset and uses the built-in baseline' do
            out = StringIO.new
            code = described_class.run(env: { 'XTIDE_BASELINE' => '' }, out: out, err: StringIO.new)
            expect(code).to eq(HarmonicsReleaseCheck::EXIT_OK)
            expect(out.string).to include("known: #{HarmonicsReleaseCheck::KNOWN_XTIDE_RELEASE}")
        end

        it 'treats an empty TICON_BASELINE as unset and uses the built-in baseline' do
            out = StringIO.new
            code = described_class.run(env: { 'TICON_BASELINE' => '' }, out: out, err: StringIO.new)
            expect(code).to eq(HarmonicsReleaseCheck::EXIT_OK)
            expect(out.string).to include("known: TICON-#{HarmonicsReleaseCheck::KNOWN_TICON_RELEASE}")
        end

        ['abc', '2026', '202512280', '2025-12-28', ' 20251228'].each do |value|
            it "rejects XTIDE_BASELINE=#{value.inspect} with the check-failed code" do
                err = StringIO.new
                code = described_class.run(env: { 'XTIDE_BASELINE' => value }, out: StringIO.new, err: err)
                expect(code).to eq(HarmonicsReleaseCheck::EXIT_CHECK_FAILED)
                expect(err.string).to include('XTIDE_BASELINE').and include(value.inspect)
            end
        end

        ['abc', '0', '-1', '4.0', '4x'].each do |value|
            it "rejects TICON_BASELINE=#{value.inspect} with the check-failed code" do
                err = StringIO.new
                code = described_class.run(env: { 'TICON_BASELINE' => value }, out: StringIO.new, err: err)
                expect(code).to eq(HarmonicsReleaseCheck::EXIT_CHECK_FAILED)
                expect(err.string).to include('TICON_BASELINE').and include(value.inspect)
            end
        end

        it 'returns the new-release code when TICON_BASELINE is older than the latest release' do
            out = StringIO.new
            code = described_class.run(env: { 'TICON_BASELINE' => '3' }, out: out, err: StringIO.new)
            expect(code).to eq(HarmonicsReleaseCheck::EXIT_NEW_RELEASE)
            expect(out.string).to include('NEW RELEASE TICON-4')
        end

        it 'returns the check-failed code when the step summary cannot be written' do
            err = StringIO.new
            code = described_class.run(env: { 'GITHUB_STEP_SUMMARY' => '/nonexistent/dir/summary.md' },
                                       out: StringIO.new, err: err)
            expect(code).to eq(HarmonicsReleaseCheck::EXIT_CHECK_FAILED)
            expect(err.string).to include('Errno::ENOENT')
        end
    end

    describe 'running the script as a process' do
        let(:script) { File.expand_path('../../scripts/check_harmonics_releases.rb', __dir__) }

        def run_script(env)
            _out, err, status = Open3.capture3(env, RbConfig.ruby, script)
            [status.exitstatus, err]
        end

        it 'exits 2 when XTIDE_BASELINE is invalid' do
            code, err = run_script('XTIDE_BASELINE' => 'bad', 'TICON_BASELINE' => nil)
            expect(code).to eq(HarmonicsReleaseCheck::EXIT_CHECK_FAILED)
            expect(err).to include('XTIDE_BASELINE')
        end

        it 'exits 2 when TICON_BASELINE is invalid' do
            code, err = run_script('XTIDE_BASELINE' => nil, 'TICON_BASELINE' => 'abc')
            expect(code).to eq(HarmonicsReleaseCheck::EXIT_CHECK_FAILED)
            expect(err).to include('TICON_BASELINE')
        end
    end
end

# frozen_string_literal: true

require 'json'
require 'open3'
require 'tmpdir'

RSpec.describe 'the native Tier B official comparison' do
    root = File.expand_path('../../..', __dir__)
    script = File.join(root, 'scripts/otc/vectors/tier_b.rb')
    input = ENV.fetch('OTC_TIER_B_INPUT')

    it 'applies the fixed bounds to the real cached agency predictions' do
        Dir.mktmpdir('tier-b-green-') do |out|
            log, status = Open3.capture2e(RbConfig.ruby, script, '--input', input, '--out', out)
            expect(status.success?).to be(true), log
            report = JSON.parse(File.read(File.join(out, 'comparison.json')))
            expect(report.fetch('rows').length).to eq(30)
            expect(report.fetch('rows')).to all(include('passed' => true))
        end
    end

    it 'detects the UTC+1 phase error on the same real Bergen comparison' do
        Dir.mktmpdir('tier-b-phase-red-') do |out|
            log, status = Open3.capture2e(RbConfig.ruby, script, '--input', input, '--out', out,
                                        '--only', 'kartverket/BGO/heights', '--mutant', 'raw-kartverket-phase')
            expect(status.success?).to be(false), log
            expect(File.file?(File.join(out, 'comparison.json'))).to be(true), log
            report = JSON.parse(File.read(File.join(out, 'comparison.json')))
            expect(report.fetch('rows').first.fetch('metrics').fetch('height_max_m')).to be > 0.03
        end
    end

    it 'passes the same actual Bergen hours with the accepted phase conversion' do
        Dir.mktmpdir('tier-b-phase-green-') do |out|
            log, status = Open3.capture2e(RbConfig.ruby, script, '--input', input, '--out', out,
                                        '--only', 'kartverket/BGO/heights')
            expect(status.success?).to be(true), log
            report = JSON.parse(File.read(File.join(out, 'comparison.json')))
            expect(report.fetch('rows').first.fetch('metrics').fetch('height_max_m')).to be <= 0.03
        end
    end

    it 'refuses a missing used table row rather than comparing invented predictions' do
        Dir.mktmpdir('tier-b-table-red-') do |out|
            log, status = Open3.capture2e(RbConfig.ruby, script, '--input', input, '--out', out,
                                        '--only', 'kartverket/BGO/heights', '--mutant', 'missing-used-row')
            expect(status.success?).to be(false), log
            expect(File.file?(File.join(out, 'comparison.json'))).to be(true), log
            report = JSON.parse(File.read(File.join(out, 'comparison.json')))
            expect(report.fetch('rows').first.fetch('error')).to include('code' => 'unsupported_constituent')
        end
    end

    it 'pairs the near-exact Puget event before an earlier event 86 minutes away' do
        Dir.mktmpdir('tier-b-pairing-') do |out|
            log, status = Open3.capture2e(RbConfig.ruby, script, '--input', input, '--out', out,
                                        '--only', 'noaa-currents/PUG1501:1/events')
            expect(status.success?).to be(false), log  # Genuine missed/extra events remain failures.
            report = JSON.parse(File.read(File.join(out, 'comparison.json')))
            pairs = report.fetch('rows').first.fetch('details').fetch('paired')
            pair = pairs.find { |p| p.fetch('official').fetch('time') == '2026-10-04T10:29:00Z' }
            expect(pair).not_to be_nil
            expect(pair.fetch('model').fetch('time')).to eq('2026-10-04T10:28:53Z')
            expect(pair.fetch('time_error_s')).to eq(7)
        end
    end
end

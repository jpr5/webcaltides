# frozen_string_literal: true

require 'rspec'
require 'json'
require 'open3'
require 'tmpdir'
require 'rbconfig'

RSpec.describe 'the native strict case oracle' do
    let(:script) { File.expand_path('../../../scripts/otc/vectors/run_cases.rb', __dir__) }
    let(:sdk) { ENV.fetch('OTC_ORACLE_SDK') }

    def run_oracle(out, *extra)
        Open3.capture2e(RbConfig.ruby, script,
                       '--calls', File.join(sdk, 'conformance/tools/predict_calls.json'),
                       '--fixtures', File.join(sdk, 'conformance/fixtures'),
                       '--out', out, *extra)
    end

    it 'computes actual authored cases reproducibly with bound fixture hashes and checked outcomes' do
        Dir.mktmpdir do |dir|
            out = File.join(dir, 'first')
            log, status = run_oracle(out)
            expect(status.success?).to be(true), log
            doc = JSON.parse(File.read(File.join(out, 'predict_expected.json')))
            expect(doc.fetch('cases').length).to eq(119)
            expect(doc.fetch('cases').sum { |c| c.fetch('steps').length }).to eq(253)
            expect(doc.fetch('calls_sha256')).to match(/\A[0-9a-f]{64}\z/)
            one = doc.fetch('cases').find { |c| c['id'] == 'predict-extremes/single-constituent' }
            expect(one['steps'][0]['items'].length).to eq(116)
            checks = JSON.parse(File.read(File.join(out, 'crosscheck.json')))
            expect(checks.fetch('problems')).to eq([])
            expect(checks.fetch('deferred_bands_remaining')).to eq([])
            log, status = run_oracle(File.join(dir, 'second'))
            expect(status.success?).to be(true), log
            %w[predict_expected.json crosscheck.json].each do |name|
                expect(File.binread(File.join(dir, 'second', name))).to eq(File.binread(File.join(out, name)))
            end
        end
    end

    it 'refuses a deliberately wrong real-call outcome instead of pinning it' do
        Dir.mktmpdir do |dir|
            calls = JSON.parse(File.read(File.join(sdk, 'conformance/tools/predict_calls.json')))
            calls['cases'].find { |c| c['id'] == 'predict-extremes/single-constituent' }['steps'][0]['outcome']['count'] = 115
            path = File.join(dir, 'wrong.json')
            File.write(path, JSON.generate(calls))
            log, status = run_oracle(File.join(dir, 'out'), '--calls', path, '--case', 'single-constituent')
            expect(status.success?).to be(false)
            expect(log).to include('expected count 115')
            expect(File.exist?(File.join(dir, 'out', 'predict_expected.json'))).to be(false)
        end
    end
end

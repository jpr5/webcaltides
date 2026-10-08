# frozen_string_literal: true

require 'tmpdir'
require 'stringio'
require_relative '../../scripts/build_noaa_station_types'

RSpec.describe NoaaStationTypes do
    let(:url) { NoaaStationTypes::URL }

    def response(*stations, count: stations.size)
        { 'count' => count, 'stations' => stations.map { |id, type| { 'id' => id, 'type' => type, 'name' => "Station #{id}" } } }.to_json
    end

    def run(*argv, path:)
        out = StringIO.new
        err = StringIO.new
        code = described_class.run(argv: argv, env: {}, out: out, err: err, path: path)
        [code, out.string + err.string]
    end

    before { stub_const('NoaaStationTypes::RETRY_DELAY', 0) }

    describe '.parse' do
        it 'maps "R" to harmonic and "S" to subordinate, sorted by id' do
            expect(described_class.parse(response(%w[9458779 R], %w[8531804 S])))
                .to eq({ '8531804' => 'subordinate', '9458779' => 'harmonic' })
        end

        it 'rejects an unknown type, a truncated list and an unexpected shape' do
            expect { described_class.parse(response(%w[1 X])) }.to raise_error(NoaaStationTypes::CheckError, /unexpected station/)
            expect { described_class.parse(response(%w[1 R], count: 2)) }.to raise_error(NoaaStationTypes::CheckError, /1 of 2/)
            expect { described_class.parse('[]') }.to raise_error(NoaaStationTypes::CheckError, /shape/)
            expect { described_class.parse('<html>') }.to raise_error(NoaaStationTypes::CheckError, /unparseable/)
        end
    end

    describe '.run' do
        it 'writes the file, then --check finds it up to date (exit 0)' do
            stub_request(:get, url).to_return(body: response(%w[2 S], %w[1 R]))
            Dir.mktmpdir do |dir|
                path = "#{dir}/types.json"
                expect(run(path: path).first).to eq(0)
                expect(JSON.parse(File.read(path))).to eq({ '1' => 'harmonic', '2' => 'subordinate' })
                code, output = run('--check', path: path)
                expect(code).to eq(0)
                expect(output).to include('up to date, 2 stations')
            end
        end

        it 'exits 1 under --check when NOAA changed a station, and leaves the file alone' do
            stub_request(:get, url).to_return(body: response(%w[1 S]))
            Dir.mktmpdir do |dir|
                path = "#{dir}/types.json"
                File.write(path, { '1' => 'harmonic' }.to_json)
                code, output = run('--check', path: path)
                expect(code).to eq(1)
                expect(output).to include('1: harmonic -> subordinate')
                expect(JSON.parse(File.read(path))).to eq({ '1' => 'harmonic' })
            end
        end

        it 'exits 2 when NOAA cannot be fetched, and leaves the file alone' do
            stub_request(:get, url).to_return(status: 503)
            Dir.mktmpdir do |dir|
                path = "#{dir}/types.json"
                File.write(path, '{}')
                code, output = run(path: path)
                expect(code).to eq(2)
                expect(output).to include('CHECK FAILED')
                expect(File.read(path)).to eq('{}')
            end
        end
    end

    it 'the committed data file is what the script writes (sorted, "harmonic"/"subordinate" only)' do
        types = JSON.parse(File.read(NoaaStationTypes::OUTPUT_PATH))
        expect(types.values.uniq.sort).to eq(%w[harmonic subordinate])
        expect(File.read(NoaaStationTypes::OUTPUT_PATH)).to eq(described_class.render(types.sort.to_h))
    end
end

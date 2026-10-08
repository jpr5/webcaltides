# frozen_string_literal: true

require 'digest'
require 'open3'
require 'tmpdir'
require_relative '../../../scripts/gesla/dump_tcd'

# The GESLA fit reads speed, V0+u and f from a dump of the TCD the engine reads.
# The dump must hold the engine's own values, or the fitted phases would not be in
# the engine's convention.
RSpec.describe GeslaTcdDump do
    let(:tcd_path) { Harmonics::Engine::XTIDE_FILE }
    let(:engine) { Harmonics::Engine.new(Logger.new('/dev/null')) }
    let(:dump) { described_class.dump(tcd_path) }

    describe '.dump' do
        it 'covers the years 1900 to 2100 and records the TCD SHA-256' do
            expect(dump['years']).to eq([1900, 2100])
            expect(dump['tcd_sha256']).to eq(Digest::SHA256.file(tcd_path).hexdigest)
            expect(dump['constituents']).to include('M2', 'S2', 'K1', 'O1', 'N2', 'SA', 'M1')
        end

        it "equals the engine's own V0+u and f for M2 S2 K1 O1 N2 in 2000, 2026 and 2027" do
            [2000, 2026, 2027].each do |year|
                factors = engine.send(:calculate_tcd_nodal_factors, year, 0.0)
                %w[M2 S2 K1 O1 N2].each do |name|
                    c = dump['constituents'].fetch(name)
                    i = year - dump['years'].first
                    expect(c['v0u'][i]).to be_within(1e-9).of(factors[name]['V0']), "#{name} #{year} V0+u"
                    expect(c['f'][i]).to be_within(1e-9).of(factors[name]['f']), "#{name} #{year} f"
                    expect(factors[name]['u']).to eq(0.0)
                end
            end
        end

        it 'keeps one value per year for every constituent' do
            n = dump['years'].last - dump['years'].first + 1
            dump['constituents'].each_value do |c|
                expect(c['v0u'].size).to eq(n)
                expect(c['f'].size).to eq(n)
            end
        end
    end

    describe '.tcd_constituent_name' do
        let(:tcd_names) { dump['constituents'].keys }

        it 'maps the TICON spellings to the TCD names' do
            expect(%w[MI2 NI2 LM2 EP2 MKS].map { |n| described_class.tcd_constituent_name(n, tcd_names) })
                .to eq(%w[MU2 NU2 LDA2 EPS2 MKS2])
            expect(described_class.tcd_constituent_name(' m2 ', tcd_names)).to eq('M2')
        end

        it 'fails on a name the TCD does not define' do
            expect { described_class.tcd_constituent_name('XYZ9', tcd_names) }.to raise_error(ArgumentError, /XYZ9/)
        end
    end

    describe 'the command' do
        let(:script) { File.expand_path('../../../scripts/gesla/dump_tcd.rb', __dir__) }

        it 'writes the same bytes on every run' do
            Dir.mktmpdir do |dir|
                outs = %w[a.json b.json].map do |f|
                    out = File.join(dir, f)
                    _, err, status = Open3.capture3('ruby', script, '--tcd', tcd_path, '--out', out)
                    expect(status).to be_success, err
                    File.binread(out)
                end
                expect(outs[0]).to eq(outs[1])
                expect(JSON.parse(outs[0])['constituents']).to eq(JSON.parse(JSON.generate(dump['constituents'])))
            end
        end

        it 'stops with a non-zero exit on a required name the TCD does not define' do
            Dir.mktmpdir do |dir|
                _, err, status = Open3.capture3('ruby', script, '--tcd', tcd_path, '--out', File.join(dir, 'x.json'),
                                                '--require', 'M2,XYZ9')
                expect(status).not_to be_success
                expect(err).to match(/XYZ9/)
            end
        end
    end
end

# frozen_string_literal: true

require 'json'
require 'open3'
require 'tmpdir'

RSpec.describe 'the native Tier B official comparison' do
    root = File.expand_path('../../..', __dir__)
    script = File.join(root, 'scripts/otc/vectors/tier_b.rb')
    input = ENV.fetch('OTC_TIER_B_INPUT')

    it 'preserves all fixed bounds and six actual scientific failures' do
        Dir.mktmpdir('tier-b-green-') do |out|
            log, status = Open3.capture2e(RbConfig.ruby, script, '--input', input, '--out', out,
                                        '--xtide-report', ENV.fetch('OTC_TIER_B_XTIDE_REPORT'))
            expect(status.exitstatus).to eq(1), log
            report = JSON.parse(File.read(File.join(out, 'comparison.json')))
            pinned = JSON.parse(File.read(ENV.fetch('OTC_TIER_B_COMPARISON')))
            expect(report).to eq(pinned)
            expect(report.fetch('rows').length).to eq(32)
            expect(report.fetch('rows').count { |row| row.fetch('passed') }).to eq(26)
            expect(report.fetch('passed')).to be(false)
        end
    end

    def coverage_gate(release:, comparison: ENV.fetch('OTC_TIER_B_COMPARISON'))
        Dir.mktmpdir('tier-b-coverage-') do |out|
            args = [RbConfig.ruby, File.expand_path('../../../scripts/otc/vectors/tier_b.rb', __dir__),
                    '--input', ENV.fetch('OTC_TIER_B_INPUT'), '--out', out,
                    '--coverage-release', release, '--comparison', comparison,
                    '--provenance', ENV.fetch('OTC_TIER_B_PROVENANCE'),
                    '--inventory', ENV.fetch('OTC_TIER_B_INVENTORY')]
            args << '--projection' if ENV['OTC_TIER_B_PROJECTION'] == '1'
            log, status = Open3.capture2e(*args)
            path = File.join(out, 'coverage.json')
            [log, status, File.file?(path) ? JSON.parse(File.read(path)) : nil]
        end
    end

    def changed_document(path)
        Dir.mktmpdir('tier-b-negative-') do |out|
            document = JSON.parse(File.read(path))
            yield(document, File.join(out, 'candidate.json'))
        end
    end

    it 'accepts all32 unchanged subjects only with exact retained metric labels' do
        log, status, report = coverage_gate(release: ENV.fetch('OTC_TIER_B_RELEASE'))
        expect(status.success?).to be(true), log
        kind = ENV['OTC_TIER_B_PROJECTION'] == '1' ? 'source-derived-labelled-projection' : 'release-candidate'
        expect(report).to include('passed' => true, 'scientific_passed' => false,
                                  'scientific_passes' => 26, 'scientific_failures' => 6,
                                  'artifact_kind' => kind)
        expect(report.fetch('mappings').length).to eq(32)
        expect(report.fetch('labels').length).to eq(6)
    end

    it 'rejects the same actual old release without retained labels' do
        log, status, report = coverage_gate(release: ENV.fetch('OTC_TIER_B_OLD_RELEASE'))
        expect(status.success?).to be(false), log
        expect(report.fetch('violations').join).to include('missing source record')
    end

    it 'rejects an omitted bin-specific label on the actual candidate' do
        changed_document(ENV.fetch('OTC_TIER_B_RELEASE')) do |document, path|
            record = document.fetch('stations').find { |station| station['station_id'] == 'OTC-f2ecceab' }.fetch('source_records').first
            record['detail'] = record.fetch('detail').lines.reject { |line| line.include?('BOS1101:15/events') }.join
            File.write(path, JSON.generate(document))
            log, status, report = coverage_gate(release: path)
            expect(status.success?).to be(false), log
            expect(report.fetch('violations').join).to include('missing metric label')
        end
    end

    it 'rejects a label assigned to the wrong current bin' do
        changed_document(ENV.fetch('OTC_TIER_B_RELEASE')) do |document, path|
            record = document.fetch('stations').find { |station| station['station_id'] == 'OTC-f2ecceab' }.fetch('source_records').first
            record['detail'] = record.fetch('detail').gsub('"bin_id":15', '"bin_id":14')
            File.write(path, JSON.generate(document))
            log, status, report = coverage_gate(release: path)
            expect(status.success?).to be(false), log
            expect(report.fetch('violations').join).to include('bin_id')
        end
    end

    it 'rejects a changed actual source coefficient' do
        changed_document(ENV.fetch('OTC_TIER_B_RELEASE')) do |document, path|
            station = document.fetch('stations').find { |item| item['station_id'] == 'OTC-f2ecceab' }
            set = station.fetch('constant_sets').find { |item| item['source'] == 'noaa-currents' }
            bin = set.fetch('current_bins').find { |item| item['bin'] == 1 }
            bin.fetch('constituents').first['major_amplitude_ms'] += 0.0001
            File.write(path, JSON.generate(document))
            log, status, report = coverage_gate(release: path)
            expect(status.success?).to be(false), log
            expect(report.fetch('violations').join).to include('source values changed')
        end
    end

    it 'rejects a missing scientific comparison row' do
        changed_document(ENV.fetch('OTC_TIER_B_COMPARISON')) do |document, path|
            document.fetch('rows').pop
            File.write(path, JSON.generate(document))
            log, status, report = coverage_gate(release: ENV.fetch('OTC_TIER_B_RELEASE'), comparison: path)
            expect(status.success?).to be(false), log
            expect(report.fetch('violations').join).to include('comparison hash')
        end
    end

    it 'rejects a widened original scientific bound' do
        changed_document(ENV.fetch('OTC_TIER_B_COMPARISON')) do |document, path|
            document.fetch('rows').find { |row| row['id'] == 'kartverket/BGO/extremes' }.fetch('limits')['time_max_s'] = 182
            File.write(path, JSON.generate(document))
            log, status, report = coverage_gate(release: ENV.fetch('OTC_TIER_B_RELEASE'), comparison: path)
            expect(status.success?).to be(false), log
            expect(report.fetch('violations').join).to include('comparison hash')
        end
    end

    it 'rejects a wrong subordinate reference bin in the retained label' do
        changed_document(ENV.fetch('OTC_TIER_B_RELEASE')) do |document, path|
            record = document.fetch('stations').find { |station| station['station_id'] == 'OTC-c93802ca' }.fetch('source_records').first
            record['detail'] = record.fetch('detail').gsub('"reference_bin_id":11', '"reference_bin_id":6')
            File.write(path, JSON.generate(document))
            log, status, report = coverage_gate(release: path)
            expect(status.success?).to be(false), log
            expect(report.fetch('violations').join).to include('reference_bin_id')
        end
    end

    it 'rejects a false blanket Bergen hourly height failure' do
        changed_document(ENV.fetch('OTC_TIER_B_RELEASE')) do |document, path|
            station = document.fetch('stations').find { |item| item['station_id'] == 'OTC-e75bb97b' }
            record = station.fetch('source_records').find { |item| item['source'] == 'kartverket' }
            record['detail'] += "\nHourly heights fail."
            File.write(path, JSON.generate(document))
            log, status, report = coverage_gate(release: path)
            expect(status.success?).to be(false), log
            expect(report.fetch('violations').join).to include('blanket BGO hourly height failure')
        end
    end

    it 'retains the actual metadata-only BGO source version and its separate tested-set binding' do
        log, status, report = coverage_gate(release: ENV.fetch('OTC_TIER_B_RELEASE'))
        expect(status.success?).to be(true), log
        label = report.fetch('labels').find { |item| item['row_id'] == 'kartverket/BGO/extremes' }
        expect(label.fetch('source_version')).to eq('20261008')
        document = JSON.parse(File.read(ENV.fetch('OTC_TIER_B_RELEASE')))
        station = document.fetch('stations').find { |item| item['station_id'] == 'OTC-e75bb97b' }
        record = station.fetch('source_records').find { |item| item['source'] == 'kartverket' }
        expect(record).to include('role' => 'gauge_station', 'disposition' => 'metadata_only', 'published_set_ids' => [])
    end

    it 'accepts the actual metadata-only ACT offset model without an invented own set' do
        log, status, report = coverage_gate(release: ENV.fetch('OTC_TIER_B_RELEASE'))
        expect(status.success?).to be(true), log
        mapping = report.fetch('mappings').find { |item| item['row_id'] == 'noaa-currents/ACT0091:1/events' }
        expect(mapping).to include('set_id' => nil, 'reference_station_id' => 'OTC-6e5989a2',
                                   'reference_set_id' => 'OTC-6e5989a2/noaa-currents/EPT0003', 'reference_bin_id' => 11)
    end

    {
        'parent' => 'source values changed',
        'bin' => 'source values changed',
        'version' => 'ACT tested reference source version changed',
        'offset' => 'source values changed',
        'reference input hash' => 'ACT tested reference source input hashes',
        'original raw hash' => 'original input provenance changed',
        'metric label' => 'metric label differs',
        'own-set link' => 'invented ACT own-set ownership'
    }.each do |change, violation|
        it "rejects an ACT offset association with changed #{change}" do
            changed_document(ENV.fetch('OTC_TIER_B_RELEASE')) do |document, path|
                station = document.fetch('stations').find { |item| item['station_id'] == 'OTC-c93802ca' }
                record = station.fetch('source_records').find { |item| item['source'] == 'noaa-currents' && item['source_record_id'] == 'ACT0091' }
                offset = station.fetch('current_offsets').find { |item| item['bin'] == 1 }
                reference = document.fetch('stations').find { |item| item['station_id'] == 'OTC-6e5989a2' }
                set = reference.fetch('constant_sets').find { |item| item['set_id'] == 'OTC-6e5989a2/noaa-currents/EPT0003' }
                case change
                when 'parent'
                    offset['reference_station_id'] = 'OTC-f2ecceab'
                when 'bin'
                    offset['reference_bin'] = 6
                when 'version'
                    set['source_version'] = '20261010'
                when 'offset'
                    offset['flood_amp_ratio'] += 0.0001
                when 'reference input hash'
                    set.fetch('provenance').fetch('input_sha256')[0] = '0' * 64
                when 'original raw hash'
                    record.fetch('provenance').fetch('input_sha256')[0] = '0' * 64
                when 'metric label'
                    record['detail'] = record.fetch('detail').gsub('"event_extra":2', '"event_extra":0')
                when 'own-set link'
                    record['published_set_ids'] = [set.fetch('set_id')]
                end
                File.write(path, JSON.generate(document))
                log, status, report = coverage_gate(release: path)
                expect(status.success?).to be(false), log
                expect(report.fetch('violations').join).to include(violation)
            end
        end
    end

    %w[original_source_version tested_set_source_version tested_input_sha256 tested_constituents_sha256 set_id comparison_sha256].each do |field|
        it "rejects an altered BGO tested-set binding #{field}" do
            changed_document(ENV.fetch('OTC_TIER_B_RELEASE')) do |document, path|
                station = document.fetch('stations').find { |item| item['station_id'] == 'OTC-e75bb97b' }
                record = station.fetch('source_records').find { |item| item['source'] == 'kartverket' }
                metadata = record.fetch('original_metadata')
                binding = JSON.parse(metadata.fetch('OTC_metric_tested_set_bindings_json'))
                binding.first[field] = if field == 'tested_input_sha256'
                                           binding.first.fetch(field).each_with_index.map { |hash, index| index.zero? ? '0' * 64 : hash }
                                       elsif field.end_with?('sha256')
                                           '0' * 64
                                       elsif field == 'set_id'
                                           'OTC-e75bb97b/gesla-fit/bergen-bgo-nor-nhs'
                                       else
                                           '20261011'
                                       end
                metadata['OTC_metric_tested_set_bindings_json'] = JSON.generate(binding)
                File.write(path, JSON.generate(document))
                log, status, report = coverage_gate(release: path)
                expect(status.success?).to be(false), log
                expect(report.fetch('violations').join).to include('BGO tested-set binding')
            end
        end
    end

    it 'rejects a false cross-version BGO published-set ownership link' do
        changed_document(ENV.fetch('OTC_TIER_B_RELEASE')) do |document, path|
            station = document.fetch('stations').find { |item| item['station_id'] == 'OTC-e75bb97b' }
            record = station.fetch('source_records').find { |item| item['source'] == 'kartverket' }
            record['published_set_ids'] = ['OTC-e75bb97b/kartverket/BGO']
            File.write(path, JSON.generate(document))
            log, status, report = coverage_gate(release: path)
            expect(status.success?).to be(false), log
            expect(report.fetch('violations').join).to include('invalid cross-version BGO published-set link')
        end
    end

    it 'rejects a wrong source version on the actual tested BGO set' do
        changed_document(ENV.fetch('OTC_TIER_B_RELEASE')) do |document, path|
            station = document.fetch('stations').find { |item| item['station_id'] == 'OTC-e75bb97b' }
            station.fetch('constant_sets').find { |item| item['source'] == 'kartverket' }['source_version'] = '20261008'
            File.write(path, JSON.generate(document))
            log, status, report = coverage_gate(release: path)
            expect(status.success?).to be(false), log
            expect(report.fetch('violations').join).to include('tested BGO source version changed')
        end
    end

    it 'rejects a discarded original constituent value' do
        changed_document(ENV.fetch('OTC_TIER_B_RELEASE')) do |document, path|
            station = document.fetch('stations').find { |item| item['station_id'] == 'OTC-e75bb97b' }
            station.fetch('constant_sets').find { |item| item['source'] == 'kartverket' }.fetch('constituents').pop
            File.write(path, JSON.generate(document))
            log, status, report = coverage_gate(release: path)
            expect(status.success?).to be(false), log
            expect(report.fetch('violations').join).to include('source values changed')
        end
    end

    it 'rejects altered source-record input provenance' do
        changed_document(ENV.fetch('OTC_TIER_B_RELEASE')) do |document, path|
            record = document.fetch('stations').find { |station| station['station_id'] == 'OTC-f2ecceab' }.fetch('source_records').first
            record.fetch('provenance').fetch('input_sha256').pop
            File.write(path, JSON.generate(document))
            log, status, report = coverage_gate(release: path)
            expect(status.success?).to be(false), log
            expect(report.fetch('violations').join).to include('original input provenance changed')
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

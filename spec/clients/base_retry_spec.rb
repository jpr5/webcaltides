# frozen_string_literal: true

require 'mechanize'

RSpec.describe Clients::Base do
    let(:null_logger) { Logger.new(nil) } # Null logger for tests
    let(:test_client) { Clients::Base.new(null_logger) }
    let(:test_url) { 'https://example.com/api/data' }

    describe 'timeout retry with exponential backoff' do
        it 'retries on Net::ReadTimeout with exponential backoff' do
            retries = 0
            allow_any_instance_of(Mechanize).to receive(:get) do
                retries += 1
                if retries < 3
                    raise Net::ReadTimeout.new('execution expired')
                else
                    double('response', body: '{"success": true}')
                end
            end

            allow(test_client).to receive(:sleep) # Don't actually sleep in tests

            result = test_client.send(:get_url, test_url)

            expect(result).to eq('{"success": true}')
            expect(retries).to eq(3) # Initial + 2 retries
        end

        it 'retries on Net::OpenTimeout with exponential backoff' do
            retries = 0
            allow_any_instance_of(Mechanize).to receive(:get) do
                retries += 1
                if retries < 2
                    raise Net::OpenTimeout.new('execution expired')
                else
                    double('response', body: '{"success": true}')
                end
            end

            allow(test_client).to receive(:sleep)

            result = test_client.send(:get_url, test_url)

            expect(result).to eq('{"success": true}')
            expect(retries).to eq(2) # Initial + 1 retry
        end

        it 'gives up after 5 retries and re-raises' do
            allow_any_instance_of(Mechanize).to receive(:get).and_raise(Net::ReadTimeout.new('execution expired'))
            allow(test_client).to receive(:sleep)

            expect {
                test_client.send(:get_url, test_url)
            }.to raise_error(Net::ReadTimeout, /execution expired/)
        end

        it 'uses exponential backoff (verifies delay calculation)' do
            retries = 0
            allow_any_instance_of(Mechanize).to receive(:get) do
                retries += 1
                raise Net::ReadTimeout.new('timeout') if retries <= 5
                double('response', body: '{}')
            end

            delays = []
            allow(test_client).to receive(:sleep) do |delay|
                delays << delay
            end

            # Should fail after 5 retries
            begin
                test_client.send(:get_url, test_url)
            rescue Net::ReadTimeout
                # Expected
            end

            # Verify delays use exponential formula: rand(0.5..(2.0 ** retry_count))
            expect(delays.size).to eq(5)
            delays.each_with_index do |delay, idx|
                retry_count = idx + 1
                expected_min = 0.5
                expected_max = 2.0 ** retry_count

                expect(delay).to be >= expected_min
                expect(delay).to be <= expected_max
            end
        end
    end

    describe 'HTTP error retry with exponential backoff' do
        it 'retries on 502 error with exponential backoff' do
            retries = 0
            allow_any_instance_of(Mechanize).to receive(:get) do
                retries += 1
                if retries < 3
                    error = Mechanize::ResponseCodeError.new(double('page', uri: URI(test_url), code: '502', body: 'Bad Gateway'))
                    raise error
                else
                    double('response', body: '{"success": true}')
                end
            end

            allow(test_client).to receive(:sleep)

            result = test_client.send(:get_url, test_url)

            expect(result).to eq('{"success": true}')
            expect(retries).to eq(3)
        end

        it 'retries on 504 error with exponential backoff' do
            retries = 0
            allow_any_instance_of(Mechanize).to receive(:get) do
                retries += 1
                if retries < 2
                    error = Mechanize::ResponseCodeError.new(double('page', uri: URI(test_url), code: '504', body: 'Gateway Timeout'))
                    raise error
                else
                    double('response', body: '{"success": true}')
                end
            end

            allow(test_client).to receive(:sleep)

            result = test_client.send(:get_url, test_url)

            expect(result).to eq('{"success": true}')
            expect(retries).to eq(2)
        end

        it 'does not retry on other HTTP errors (e.g., 404, 500)' do
            error = Mechanize::ResponseCodeError.new(double('page', uri: URI(test_url), code: '404', body: 'Not Found'))
            allow_any_instance_of(Mechanize).to receive(:get).and_raise(error)

            expect {
                test_client.send(:get_url, test_url)
            }.to raise_error(Mechanize::ResponseCodeError)
        end
    end

    describe 'mixed error scenarios' do
        it 'handles 502 error then timeout then success' do
            call_count = 0
            allow_any_instance_of(Mechanize).to receive(:get) do
                call_count += 1
                case call_count
                when 1
                    error = Mechanize::ResponseCodeError.new(double('page', uri: URI(test_url), code: '502', body: 'Bad Gateway'))
                    raise error
                when 2
                    raise Net::ReadTimeout.new('timeout')
                else
                    double('response', body: '{"data": "success"}')
                end
            end

            allow(test_client).to receive(:sleep)

            result = test_client.send(:get_url, test_url)

            expect(result).to eq('{"data": "success"}')
            expect(call_count).to eq(3)
        end

        it 'handles timeout then 504 error then success' do
            call_count = 0
            allow_any_instance_of(Mechanize).to receive(:get) do
                call_count += 1
                case call_count
                when 1
                    raise Net::ReadTimeout.new('timeout')
                when 2
                    error = Mechanize::ResponseCodeError.new(double('page', uri: URI(test_url), code: '504', body: 'Gateway Timeout'))
                    raise error
                else
                    double('response', body: '{"data": "success"}')
                end
            end

            allow(test_client).to receive(:sleep)

            result = test_client.send(:get_url, test_url)

            expect(result).to eq('{"data": "success"}')
            expect(call_count).to eq(3)
        end
    end

    describe 'logging' do
        let(:logger) { instance_double(Logger, debug: nil, warn: nil, error: nil) }

        before do
            allow(test_client).to receive(:logger).and_return(logger)
            allow(test_client).to receive(:sleep)
        end

        it 'logs timeout retries with attempt count' do
            retries = 0
            allow_any_instance_of(Mechanize).to receive(:get) do
                retries += 1
                if retries < 3
                    raise Net::ReadTimeout.new('timeout')
                else
                    double('response', body: '{}')
                end
            end

            test_client.send(:get_url, test_url)

            expect(logger).to have_received(:warn).with(/timeout for https:\/\/example.com\/api\/data, retry 1\/5/).once
            expect(logger).to have_received(:warn).with(/timeout for https:\/\/example.com\/api\/data, retry 2\/5/).once
        end

        it 'logs 502/504 retries with attempt count' do
            retries = 0
            allow_any_instance_of(Mechanize).to receive(:get) do
                retries += 1
                if retries < 3
                    error = Mechanize::ResponseCodeError.new(double('page', uri: URI(test_url), code: '502', body: 'Bad Gateway'))
                    raise error
                else
                    double('response', body: '{}')
                end
            end

            test_client.send(:get_url, test_url)

            expect(logger).to have_received(:warn).with(/502 from https:\/\/example.com\/api\/data, retry 1\/5/).once
            expect(logger).to have_received(:warn).with(/502 from https:\/\/example.com\/api\/data, retry 2\/5/).once
        end

        it 'logs final error after max retries' do
            allow_any_instance_of(Mechanize).to receive(:get).and_raise(Net::ReadTimeout.new('timeout'))

            begin
                test_client.send(:get_url, test_url)
            rescue Net::ReadTimeout
                # Expected
            end

            expect(logger).to have_received(:error).with(/timeout after 5 retries/).once
        end
    end
end

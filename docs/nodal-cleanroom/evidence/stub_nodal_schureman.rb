# frozen_string_literal: true

# Empty stub, used only to show the oracle test fails before implementation.
module NodalSchureman
    CONSTITUENTS = [].freeze

    def self.compute(_year, month:, day:, hour: 12, shift_hours: 0)
        {}
    end

    def self.factor(_name, _year, month:, day:, hour: 12, shift_hours: 0)
        nil
    end
end

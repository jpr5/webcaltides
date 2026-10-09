# Empty stub, used only to observe RED.
module NodalSchureman
    CONSTITUENTS = [].freeze

    def self.compute(_year, month:, day:, hour: 12, shift_hours: 0.0)
        {}
    end

    def self.constituent(_name, _year, month:, day:, hour: 12, shift_hours: 0.0)
        nil
    end
end

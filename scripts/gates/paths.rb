# Where the gate harness keeps its data. The code lives here, in scripts/gates; the
# references, caches, results and logs live outside git under OTC_GATES_DIR
# (default $OTC_WORK/gates, OTC_WORK default ~/.local/share/opentideconstants/work).
# scripts/gates/fetch_refs.rb fills it from the private mirror (data/gates/refs.lock.json).
module GatePaths
    ROOT = File.expand_path(ENV['OTC_GATES_DIR'] || File.join(ENV['OTC_WORK'] || '~/.local/share/opentideconstants/work', 'gates'))
    TOOLS = __dir__
    EVID = "#{ROOT}/evid"         # was cr/webcaltides-harmonics-testplan-evidence
    BASELINE = "#{ROOT}/baseline" # was cr/webcaltides-harmonics-baseline-2026-10-06
    B = "#{ROOT}/b"               # was cr/webcaltides-harmonics-b (gate inputs)
end

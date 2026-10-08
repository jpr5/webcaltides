#!/usr/bin/env ruby
# Fetch the gate harness reference caches by SHA-256 from the private R2 work bucket.
#
# The official-prediction caches (NOAA, BSH, Kartverket, RWS, CHS, IMI, LINZ) may not be
# redistributable, so they are never committed. They live in the private bucket
# (s3://$OTC_WORK_BUCKET/refs/<release>/, with a MANIFEST.json); data/gates/refs.lock.json
# names the release and pins every asset's SHA-256 and the digest of the unpacked tree.
#
# Credentials: in CI the OTC_WORK_ACCESS_KEY_ID, OTC_WORK_SECRET_ACCESS_KEY, OTC_WORK_BUCKET
# and R2_ACCOUNT_ID variables; locally ~/.config/opentideconstants/r2-work.env (or
# OTC_WORK_ENV). Needs the aws CLI.
#
# Usage:
#   ruby scripts/gates/fetch_refs.rb            # download, verify, unpack into OTC_GATES_DIR
#   ruby scripts/gates/fetch_refs.rb --check    # verify an unpacked OTC_GATES_DIR against the lock
#   ruby scripts/gates/fetch_refs.rb --pack <staging dir> <out dir> <release>
#                                               # build the assets and rewrite the lock (maintainers;
#                                               # then upload them to refs/<release>/ in the bucket)
# Any SHA-256 mismatch exits non-zero and names the asset or the group.
require 'json'
require 'digest'
require 'fileutils'
require 'open3'
require 'tmpdir'
require_relative 'paths'

LOCK = File.expand_path('../../data/gates/refs.lock.json', __dir__)
ENV_FILE = File.expand_path(ENV['OTC_WORK_ENV'] || '~/.config/opentideconstants/r2-work.env')

# One asset per group; each group is a set of top-level entries of the staging dir.
GROUPS = {
    'refs-noaa'  => %w[refs/noaa],
    'refs-other' => %w[refs/bsh refs/chs refs/imi refs/kartverket refs/linz refs/rws],
    'inputs'     => %w[evid sets safety_set.json baseline b builds],
}.freeze

def sha256_file(path) = Digest::SHA256.file(path).hexdigest

# Digest of a tree: SHA-256 over sorted "<relative path>  <sha256>\n" lines.
def tree_digest(root, entries)
    files = entries.flat_map do |e|
        p = File.join(root, e)
        File.directory?(p) ? Dir.glob("#{p}/**/*", File::FNM_DOTMATCH).select { |f| File.file?(f) } : [p]
    end
    files.reject! { |f| File.basename(f) == '.DS_Store' }
    lines = files.map { |f| "#{f.delete_prefix("#{root}/")}  #{sha256_file(f)}\n" }.sort
    [Digest::SHA256.hexdigest(lines.join), lines.size]
end

def run!(*cmd)
    out, st = Open3.capture2e(*cmd)
    abort "command failed (#{st.exitstatus}): #{cmd.join(' ')}\n#{out}" unless st.success?
    out
end

# Environment for the aws CLI: CI secrets, else the local env file, else AWS_* as set.
def bucket_env
    if ENV['OTC_WORK_ACCESS_KEY_ID'] && ENV['R2_ACCOUNT_ID']
        env = { 'AWS_ACCESS_KEY_ID' => ENV['OTC_WORK_ACCESS_KEY_ID'],
                'AWS_SECRET_ACCESS_KEY' => ENV.fetch('OTC_WORK_SECRET_ACCESS_KEY'),
                'AWS_DEFAULT_REGION' => 'auto',
                'AWS_ENDPOINT_URL_S3' => "https://#{ENV['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
                'OTC_WORK_BUCKET' => ENV['OTC_WORK_BUCKET'] }
    elsif File.exist?(ENV_FILE)
        env = File.readlines(ENV_FILE, chomp: true).grep(/\A[A-Z0-9_]+=/).to_h do |l|
            k, v = l.split('=', 2)
            [k, v.sub(/\A(['"])(.*)\1\z/, '\\2')]
        end
    else
        env = ENV.to_h.slice('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_DEFAULT_REGION',
                             'AWS_ENDPOINT_URL_S3', 'OTC_WORK_BUCKET')
    end
    abort "no bucket credentials: set OTC_WORK_* (CI) or create #{ENV_FILE}" unless env['OTC_WORK_BUCKET'] && env['AWS_ACCESS_KEY_ID']
    env
end

def pack(stage, out_dir, release)
    FileUtils.mkdir_p(out_dir)
    assets = GROUPS.map do |group, entries|
        entries.each { |e| abort "missing in staging dir: #{e}" unless File.exist?(File.join(stage, e)) }
        name = "#{group}.tar.gz"
        path = File.join(out_dir, name)
        run!({ 'COPYFILE_DISABLE' => '1', 'GZIP' => '-n' }, 'tar', '-czf', path, '-C', stage, '--exclude', '.DS_Store', *entries)
        digest, count = tree_digest(stage, entries)
        { 'group' => group, 'name' => name, 'size' => File.size(path), 'sha256' => sha256_file(path),
          'entries' => entries, 'files' => count, 'tree_sha256' => digest }
    end
    lock = { 'store' => "s3://$OTC_WORK_BUCKET/refs/#{release}/", 'release' => release, 'private' => true,
             'note' => 'Official-prediction caches; not redistributable. Fetch with scripts/gates/fetch_refs.rb.',
             'assets' => assets }
    File.write(LOCK, JSON.pretty_generate(lock) + "\n")
    puts "wrote #{LOCK}"
    assets.each { |a| puts "#{a['name']}  #{a['size']} B  #{a['sha256']}  files=#{a['files']}" }
end

def check(lock, root)
    bad = lock['assets'].reject do |a|
        digest, count = tree_digest(root, a['entries'])
        ok = digest == a['tree_sha256'] && count == a['files']
        warn "#{a['group']}: tree SHA-256 mismatch (#{count} files, #{digest}; lock #{a['files']}, #{a['tree_sha256']})" unless ok
        ok
    end
    abort "refs check FAILED: #{bad.map { |a| a['group'] }.join(', ')}" unless bad.empty?
    puts "refs check OK: #{root} (#{lock['assets'].sum { |a| a['files'] }} files, release #{lock['release']})"
end

def fetch(lock, root)
    FileUtils.mkdir_p(root)
    env = nil
    Dir.mktmpdir('otc-refs-', root) do |tmp|
        lock['assets'].each do |a|
            marker = File.join(root, '.refs', "#{a['group']}.sha256")
            if File.exist?(marker) && File.read(marker).strip == a['sha256']
                puts "#{a['name']}: already unpacked (#{a['sha256'][0, 12]})"
                next
            end
            env ||= bucket_env
            path = File.join(tmp, a['name'])
            url = "s3://#{env['OTC_WORK_BUCKET']}/refs/#{lock['release']}/#{a['name']}"
            run!(env, 'aws', 's3', 'cp', url, path, '--only-show-errors')
            got = sha256_file(path)
            abort "#{a['name']}: SHA-256 mismatch: got #{got}, lock #{a['sha256']}" unless got == a['sha256']
            # Unpack into a staging dir, check the tree, then move the entries into place.
            stage = File.join(tmp, a['group'])
            FileUtils.mkdir_p(stage)
            run!('tar', '-xzf', path, '-C', stage)
            digest, count = tree_digest(stage, a['entries'])
            unless digest == a['tree_sha256'] && count == a['files']
                abort "#{a['name']}: unpacked tree SHA-256 mismatch (#{count} files, #{digest})"
            end
            a['entries'].each do |e|
                dst = File.join(root, e)
                FileUtils.rm_rf(dst)
                FileUtils.mkdir_p(File.dirname(dst))
                File.rename(File.join(stage, e), dst)
            end
            FileUtils.mkdir_p(File.dirname(marker))
            File.write(marker, "#{a['sha256']}\n")
            puts "#{a['name']}: verified and unpacked (#{count} files)"
        end
    end
    check(lock, root)
end

case ARGV[0]
when '--pack'
    stage, out_dir, release = ARGV[1, 3]
    abort 'usage: fetch_refs.rb --pack <staging dir> <out dir> <release>' unless release
    pack(File.expand_path(stage), File.expand_path(out_dir), release)
when '--check'
    check(JSON.parse(File.read(LOCK)), GatePaths::ROOT)
when nil
    fetch(JSON.parse(File.read(LOCK)), GatePaths::ROOT)
else
    abort 'usage: fetch_refs.rb [--check | --pack <staging dir> <out dir> <release>]'
end

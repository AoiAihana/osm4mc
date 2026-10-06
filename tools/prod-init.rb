# frozen_string_literal: true

# Initialise one environment of the combined openstreetmap-website + carto stack:
#
#   * create (or update) the first administrator
#   * create (or reuse) the OAuth applications that the web site and iD need
#   * record their identifiers in the settings file that belongs to THIS
#     environment, so development and production can hold different ones
#
# It is idempotent, so re-running it is the way to add or rotate identifiers.
#
# Normally invoked through tools/switch-env.sh. By hand:
#
#   docker compose -p osm-prod --env-file .env.prod \
#     -f docker-compose.yml -f docker-compose.prod.yml \
#     run --rm -T web bundle exec rails runner - < tools/prod-init.rb
#
# Environment variables:
#   ADMIN_NAME      default "admin"
#   ADMIN_EMAIL     default "<name>@example.com"
#   ADMIN_PASSWORD  default: a random password, printed once

require "securerandom"

# Update `key` in a YAML settings file without disturbing anything else the
# operator has put there - comments included. (Rewriting the file with
# YAML.dump would silently drop them.)
def upsert_setting(path, key, value)
  path = Pathname.new(path)
  text = path.exist? ? path.read : ""
  line = "#{key}: #{value.to_s.inspect}"
  if text.match?(/^#{Regexp.escape(key)}:\s*/)
    text = text.sub(/^#{Regexp.escape(key)}:.*$/, line)
  else
    text += "\n" unless text.empty? || text.end_with?("\n")
    text += "#{line}\n"
  end
  path.dirname.mkpath
  path.write(text)
  path
end

# This initialiser is for a real (non-development) environment. In development
# the upstream tasks are the right tool, and running this one there has real
# side effects: development has no server_url of its own (it falls back to the
# openstreetmap.example.com placeholder), so it would rewrite the redirect URI
# of the applications the development instance already uses.
if Rails.env.development? && !ENV["ALLOW_DEVELOPMENT"].to_s.match?(/\A(1|true|yes)\z/i)
  abort <<~MESSAGE
    tools/prod-init.rb initialises a NON-development environment, but Rails.env
    is "development". Use the upstream tasks for development instead:

        bundle exec rails dev:populate
        bundle exec rails oauth:register_apps["<display name>"]

    Set ALLOW_DEVELOPMENT=1 if you really want to run it here.
  MESSAGE
end

settings_path =
  if Rails.env.development?
    # development keeps the upstream convention
    Rails.root.join("config", "settings.local.yml")
  else
    # config/settings/<environment>.local.yml is loaded *after*
    # config/settings.local.yml by the config gem, so it wins - and it is
    # already gitignored by the application.
    Rails.root.join("config", "settings", "#{Rails.env}.local.yml")
  end
base_url = "#{Settings.server_protocol}://#{Settings.server_url}"
if Settings.server_url.to_s.include?("example.com")
  warn <<~WARNING
    !!
    !! Settings.server_url is still the placeholder #{Settings.server_url.inspect}.
    !! The OAuth redirect URI below will therefore be wrong. Set server_url and
    !! server_protocol in #{settings_path} first, then run this again.
    !!
  WARNING
end

puts "environment        : #{Rails.env}"
puts "public base URL    : #{base_url}"
puts "settings file      : #{settings_path}"

# --- administrator ---------------------------------------------------------
admin_name = ENV.fetch("ADMIN_NAME", "admin")
admin_email = ENV.fetch("ADMIN_EMAIL", "#{admin_name}@example.com")
provided_password = ENV["ADMIN_PASSWORD"].to_s
# RESET_PASSWORD only makes sense together with a password the operator chose;
# never overwrite a live account with an auto-generated one nobody knows.
reset_requested = ENV["RESET_PASSWORD"].to_s.match?(/\A(1|true|yes)\z/i)

# The password that was actually stored - only then may we print credentials.
# Generating one and printing it while leaving the existing account alone would
# send the operator off with a password that does not work.
admin_password = nil
generated_password = false

user = User.find_or_initialize_by(display_name: admin_name)
if user.new_record?
  admin_password = provided_password
  if admin_password.empty?
    admin_password = SecureRandom.alphanumeric(20)
    generated_password = true
  end

  user.email = admin_email
  user.pass_crypt = admin_password
  user.pass_crypt_confirmation = admin_password
  user.tou_agreed = Time.now.utc
  user.terms_seen = true
  user.terms_agreed = Time.now.utc
  user.email_valid = true
  user.data_public = true
  user.activate
  user.save!
  puts "administrator      : created #{user.display_name} <#{user.email}>"
elsif reset_requested && !provided_password.empty?
  admin_password = provided_password
  user.pass_crypt = admin_password
  user.pass_crypt_confirmation = admin_password
  user.save!
  puts "administrator      : #{user.display_name} already existed, password reset"
else
  puts "administrator      : #{user.display_name} already exists, left untouched"
  if !provided_password.empty? && !reset_requested
    warn <<~HINT
      note: ADMIN_PASSWORD was ignored because the account already exists.
            To reset the password explicitly, add -e RESET_PASSWORD=1:

              docker compose -p osm-prod --env-file .env.prod \\
                  -f docker-compose.yml -f docker-compose.prod.yml \\
                  run --rm -T -e RESET_PASSWORD=1 -e ADMIN_NAME=#{admin_name} \\
                  -e ADMIN_PASSWORD='<new password>' web \\
                  bundle exec rails runner - < tools/prod-init.rb
    HINT
  end
end
user.roles.find_or_create_by!(role: "administrator") { |record| record.granter_id = user.id }

# --- OAuth applications ----------------------------------------------------
model = Doorkeeper.config.application_model

# Application secrets are stored hashed (Doorkeeper::SecretStoring::Sha256Hash
# here - see the runner output of `Doorkeeper.config.application_secret_strategy`),
# so `plaintext_secret` is only available in the moment the record is created.
# Re-running this script on an existing application therefore CANNOT recover the
# secret; it must leave whatever is in the settings file alone rather than write
# an empty value. ROTATE_SECRETS=true recreates the applications to mint new ones.
rotate_secrets = ENV["ROTATE_SECRETS"].to_s.match?(/\A(1|true|yes)\z/i)

ensure_application = lambda do |name, scopes, confidential, owner|
  existing = model.find_by(name: name)
  if existing && !rotate_secrets
    # Only the redirect_uri and scopes are kept in sync with this instance; the
    # owner is deliberately left alone, so re-running the initialiser (or
    # running it for a different ADMIN_NAME) cannot silently re-own the
    # applications that are already in use.
    changed = existing.redirect_uri != base_url ||
              existing.scopes.to_a.sort != scopes.sort
    if changed
      existing.update!(redirect_uri: base_url, scopes: scopes)
      puts "OAuth #{name}: existing application updated (redirect_uri/scopes)"
    else
      puts "OAuth #{name}: existing application already up to date"
    end
    # `next` is essential: without it the lambda would fall through to the
    # create path below and destroy/recreate the application on every run,
    # silently invalidating the identifiers already written to the settings.
    next [existing, false]
  end

  existing&.destroy!
  created = model.create!(
    name: name,
    scopes: scopes,
    redirect_uri: base_url,
    confidential: confidential,
    owner: owner
  )
  [created, true]
end

id_application, = ensure_application.call(
  "Local iD",
  %w[read_prefs write_prefs write_api read_gpx write_gpx write_notes],
  false,
  user
)
web_application, web_created = ensure_application.call(
  "OpenStreetMap Web Site", %w[write_api write_notes], true, user
)

# The client ids are readable at any time, so they are always refreshed.
upsert_setting(settings_path, "id_application", id_application.uid)
upsert_setting(settings_path, "oauth_application", web_application.uid)

# The secret is not.
if web_created
  upsert_setting(settings_path, "oauth_key", web_application.plaintext_secret)
  puts "OAuth web secret   : newly generated and written to #{settings_path}"
else
  puts <<~SECRET
    OAuth web secret   : kept the value already in #{settings_path}
                         (application secrets are stored hashed and cannot be read
                         back; re-run with ROTATE_SECRETS=true to recreate the
                         applications and mint a new one)
  SECRET
end

puts "OAuth iD client id : #{id_application.uid}"
puts "OAuth web client id: #{web_application.uid}"
puts "identifiers written to #{settings_path}"

if generated_password
  puts <<~CREDENTIALS

    ======================================================================
    Administrator credentials (shown once, store them now):

      user:     #{user.display_name}
      password: #{admin_password}

    Set ADMIN_PASSWORD before running this again to choose your own.
    ======================================================================
  CREDENTIALS
end

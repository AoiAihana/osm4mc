# frozen_string_literal: true

# Reset the password of an existing account.
#
# Deliberately separate from tools/prod-init.rb: resetting a password should not
# also re-register OAuth applications or rewrite the settings file.
#
#   docker compose -p osm-prod --env-file .env.prod \
#       -f docker-compose.yml -f docker-compose.prod.yml \
#       run --rm -T -e ADMIN_NAME=admin -e ADMIN_PASSWORD='...' \
#       web bundle exec rails runner - < tools/reset-password.rb
#
# Normally invoked through ./tools/switch-env.sh passwd <test|prod> [name] [password].

name = ENV.fetch("ADMIN_NAME", "admin")
password = ENV["ADMIN_PASSWORD"].to_s

abort "ADMIN_PASSWORD must be set" if password.empty?

user = User.find_by(display_name: name)
abort "no account named #{name.inspect} in this environment" if user.nil?

# pass_crypt= is a virtual attribute: the model hashes it into pass_crypt /
# pass_salt in a before_validation callback.
user.pass_crypt = password
user.pass_crypt_confirmation = password
user.save!

puts "password updated for #{user.display_name} <#{user.email}> (id #{user.id})"

#!/bin/sh
# Installs a service's secrets on the server, from a KeePassXC database.
# Run it on your own computer, from the repository:
#
#   deploy/install-secrets.sh <database.kdbx> <ssh destination> [service]
#
# Each entry in the database's gift-exchange/<service> group ("app" by
# default) becomes a file on the server named after the entry's title,
# holding its password: /etc/gift-exchange/secrets/<service>/<title>. The
# files are readable only by root and the containers' user (uid 10001).
# Values go from KeePassXC to the server through pipes, never into a file
# on this computer or onto a command line. The SSH destination must log
# in as root. Restart the service afterwards, to read them.
set -eu
[ $# -ge 2 ] || { sed -n '4,6p' "$0" >&2; exit 2; }
db=$1 dest=$2 service=${3:-app}
group="gift-exchange/$service"
dir="/etc/gift-exchange/secrets/$service"
ssh=${SSH:-ssh} # Tests replace it

printf 'Password for %s: ' "$db" >&2
if [ -t 0 ]; then
    stty -echo
    trap 'stty echo' EXIT
fi
IFS= read -r password
printf '\n' >&2

# keepassxc-cli reads the database's password from its input.
kp() { printf '%s\n' "$password" | keepassxc-cli "$@"; }

# Entries, not subgroups (which end in "/").
names=$(kp ls --quiet "$db" "$group" | grep -v '/$') || {
    echo "No entries in $group" >&2
    exit 1
}
for name in $names; do
    case $name in
    *[!a-z0-9_]*) echo "Not a secret file name: $name" >&2 && exit 1 ;;
    esac
done

# On the server: write the file next to the old one, then swap it in.
receive='set -eu
d=$1 f=$2
install -d -m 0750 -o root -g 10001 "$d"
umask 0027
cat > "$d/.$f.new"
chgrp 10001 "$d/.$f.new"
mv -f "$d/.$f.new" "$d/$f"'
for name in $names; do
    kp show --quiet --attributes Password "$db" "$group/$name" |
        $ssh "$dest" "sh -c '$receive' - $dir $name"
    echo "Installed $dir/$name"
done

# Files on the server that aren't in KeePassXC (such as an old email
# provider's keys) are left alone, but listed.
for file in $($ssh "$dest" "ls $dir"); do
    case " $(echo $names) " in
    *" $file "*) ;;
    *) echo "Not in KeePassXC, left as it is: $dir/$file" ;;
    esac
done

#!/bin/bash

set -o nounset

# Run clean-up operations for a MacPorts installation than has become
# a little... chubby.

# Run every few months, or whenever you run out of room to torrent the 53rd
# season of Survivor.


# Check platform
export PLATFORM=$(uname)
if [ $PLATFORM != "Darwin" ]; then
    echo "Mac OS X not detected, aborting"
    exit 1;
fi

# Check permissions
if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root.  Did you sudo?" 1>&2
   exit 1
fi


# Clean installed
echo "Clean installed ports"
port clean --all -f installed


# Uninstall inactive
echo; echo "Uninstall inactive ports"
port -f uninstall inactive


# Uninstall leaves.
# Repeat until all leaves exhausted.
echo; echo "Uninstall orphaned ports"
while port uninstall leaves; do :; done

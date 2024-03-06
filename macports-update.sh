#!/bin/bash

set -o nounset
set -o errexit
set +o xtrace


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


# Update metadata?
port selfupdate
echo
port outdated


# Prompt for upgrade
echo
read -p "Do you want to upgrade? [Y/n]" -n 1 -r REPLY
echo
if [[ $REPLY =~ ^[Nn]$ ]]
then
    exit
fi

port -Rucv upgrade outdated

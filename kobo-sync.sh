#!/bin/bash

################################################
# Synchronise my Kobo eReader with local files #
################################################

set -o errexit
set -o nounset

# Config
LOCAL_FOLDER='/home/leon/Dropbox/Apps/Rakuten Kobo/'
DEVICE_FOLDER='/media/leon/KOBOeReader/'


# Check
if [ ! -d "$LOCAL_FOLDER" ]; then
  echo "Local folder does not exist: $LOCAL_FOLDER"
  exit 1
fi

if [ ! -d "$DEVICE_FOLDER" ]; then
  echo "Device folder does not exist: $DEVICE_FOLDER"
  exit 2
fi


# Dry-run
COMMAND='rsync "$LOCAL_FOLDER" "$DEVICE_FOLDER" -rtkvh --modify-window=2 '
COMMAND+="--delete-delay --stats --exclude=.kobo/ "
COMMAND+="--exclude=.adobe-digital-editions/ --exclude=.kobo-images/"
echo "DRY RUN"
echo "$COMMAND -n"
eval "$COMMAND -n"

# Live?
echo
echo "###########################"
echo "# Does this look correct? #"
echo "###########################"
read -p "Type 'yes' to execute: "
if [[ "$REPLY" =~ ^[yY].* ]];
then
    echo
    echo "$COMMAND"
    eval "$COMMAND"
else
    echo "Aborted"
fi

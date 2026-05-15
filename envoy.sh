#!/bin/bash


WATTS=0

while true;
do
    DATE=$(date --rfc-email)
    WATTS=$(curl -s http://192.168.0.43/api/v1/production | jq '.wattsNow')
    echo "$DATE ${WATTS}W"
    sleep 900
done

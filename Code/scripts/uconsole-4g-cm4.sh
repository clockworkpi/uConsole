#!/bin/bash

function tip {

echo "use mmcli -L to see 4G modem or not"
}

function enable4g {

echo "Power on 4G module on uConsole cm4"

sudo pinctrl set 24 op dh

sudo pinctrl set 15 op dh

sleep 5
sudo pinctrl set 15 dl

echo "waiting..."
sleep 13
echo "done"

}

function disable4g {
echo "Power off 4G module"

sudo pinctrl set 24 op dl

sudo pinctrl set 24 dh
sleep 3
sudo pinctrl set 24 dl

sleep 20

echo "Done"

}
if [ "$#" -ne 1 ] ; then
        echo "$0: enable/disable"
        exit 3
fi


if [ $1 == "enable" ]; then

enable4g;
tip;

fi


if [ $1 == "disable" ]; then
 disable4g
 tip;
fi

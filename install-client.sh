#!/bin/bash

if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root (use sudo)" 1>&2
   exit 1
fi

is_Raspberry=$(cat /proc/device-tree/model | awk  '{print $1}')
if [ "x${is_Raspberry}" != "xRaspberry" ] ; then
  echo "Sorry, this drivers only works on raspberry pi"
  exit 1
fi

apt update
apt upgrade -y
apt-get -y install python3 python3-pip remotetrx
apt-get -y install i2c-tools alsa-utils
pip install pyserial --break-system-packages

sed -i -e 's:#dtparam=i2c_arm=on:dtparam=i2c_arm=on:g'  /boot/firmware/config.txt || true
grep -q "dtoverlay=i2s-mmap" /boot/firmware/config.txt || \
  echo "dtoverlay=i2s-mmap" >> /boot/firmware/config.txt

grep -q "enable_uart=1" /boot/firmware/config.txt || \
  echo "enable_uart=1" >> /boot/firmware/config.txt

grep -q "dtparam=i2s=on" /boot/firmware/config.txt || \
  echo "dtparam=i2s=on" >> /boot/firmware/config.txt

grep -q "dtparam=i2c_arm=on" /boot/firmware/config.txt || \
  echo "dtparam=i2c_arm=on" >> /boot/firmware/config.txt

grep -q "dtparam=spi=on" /boot/firmware/config.txt || \
  echo "dtparam=spi=on" >> /boot/firmware/config.txt

grep -q "dtoverlay=wm8960-soundcard" /boot/firmware/config.txt || \
  echo "dtoverlay=wm8960-soundcard" >> /boot/firmware/config.txt

mkdir /etc/piremote || true
cp client/*.conf /etc/piremote
cp client/piremote-client.py /etc/piremote
cp client/piremote.service /etc/systemd/system/

systemctl daemon-reload
systemctl enable piremote.service
systemctl disable remotetrx
systemctl disable svxlink

echo "------------------------------------------------------"
echo "Please reboot to apply all settings!"
echo "------------------------------------------------------"

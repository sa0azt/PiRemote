#!/bin/bash
set -e

echo "PiRemote Installation Script"
echo "============================"

# Check if running as root
if [[ $EUID -eq 0 ]]; then
   echo "This script should not be run as root. Please run as a regular user with sudo access."
   exit 1
fi

# Detect installation type
echo "Select installation type:"
echo "1) Server (radio end)"
echo "2) Client (control panel end)"
echo "3) Both (development/testing)"
read -p "Enter choice [1-3]: " INSTALL_TYPE

case $INSTALL_TYPE in
    1) MODE="server" ;;
    2) MODE="client" ;;
    3) MODE="both" ;;
    *) echo "Invalid choice"; exit 1 ;;
esac

echo "Installing for mode: $MODE"

# Update system
echo "Updating system packages..."
sudo apt update
sudo apt upgrade -y

# Install Python dependencies
echo "Installing Python dependencies..."
sudo apt install -y python3 python3-pip python3-dev python3-setuptools

# Install common dependencies
echo "Installing common Python packages..."
pip3 install --user pyserial configparser

# Install client-specific dependencies
if [[ "$MODE" == "client" || "$MODE" == "both" ]]; then
    echo "Installing client dependencies..."
    sudo apt install -y libasound2-dev portaudio19-dev
    pip3 install --user RPi.GPIO pyaudio opuslib
fi

# Install server-specific dependencies
if [[ "$MODE" == "server" || "$MODE" == "both" ]]; then
    echo "Installing server dependencies..."
    sudo apt install -y libasound2-dev portaudio19-dev
    pip3 install --user pyaudio opuslib
fi

# Create piremote user and group
echo "Creating piremote user..."
sudo useradd -r -s /bin/false -d /etc/piremote piremote 2>/dev/null || true
sudo usermod -a -G dialout,audio,gpio piremote 2>/dev/null || true

# Create directories
echo "Creating directories..."
sudo mkdir -p /etc/piremote
sudo mkdir -p /var/log
sudo touch /var/log/piremote-server.log
sudo touch /var/log/piremote-server-error.log
sudo touch /var/log/piremote-client.log
sudo touch /var/log/piremote-client-error.log

# Set permissions
sudo chown -R piremote:piremote /etc/piremote
sudo chown piremote:piremote /var/log/piremote*.log

# Copy configuration file
echo "Installing configuration..."
if [[ ! -f /etc/piremote/piremote.conf ]]; then
    sudo cp piremote.conf /etc/piremote/
    sudo chown piremote:piremote /etc/piremote/piremote.conf
    echo "Configuration installed. Please edit /etc/piremote/piremote.conf"
else
    echo "Configuration already exists, skipping..."
fi

# Install Python files
echo "Installing Python modules..."
if [[ "$MODE" == "server" || "$MODE" == "both" ]]; then
    sudo cp server.py /etc/piremote/
    sudo cp audio.py /etc/piremote/
    sudo chown piremote:piremote /etc/piremote/server.py /etc/piremote/audio.py
    sudo chmod +x /etc/piremote/server.py
fi

if [[ "$MODE" == "client" || "$MODE" == "both" ]]; then
    sudo cp client.py /etc/piremote/
    sudo chown piremote:piremote /etc/piremote/client.py
    sudo chmod +x /etc/piremote/client.py
fi

# Install systemd services
echo "Installing systemd services..."
if [[ "$MODE" == "server" || "$MODE" == "both" ]]; then
    sudo cp piremote-server.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable piremote-server.service
    echo "Server service installed and enabled"
fi

if [[ "$MODE" == "client" || "$MODE" == "both" ]]; then
    sudo cp piremote-client.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable piremote-client.service
    echo "Client service installed and enabled"
fi

# Enable SPI and Serial if on Raspberry Pi
CONFIG_FILE=""
if [[ -f /boot/firmware/config.txt ]]; then
    CONFIG_FILE="/boot/firmware/config.txt"
    echo "Detected newer Raspberry Pi OS (config in /boot/firmware/)"
elif [[ -f /boot/config.txt ]]; then
    CONFIG_FILE="/boot/config.txt"
    echo "Detected older Raspberry Pi OS (config in /boot/)"
fi

if [[ -n "$CONFIG_FILE" ]]; then
    echo "Configuring Raspberry Pi settings in $CONFIG_FILE..."
    
    # Enable UART
    if ! grep -q "enable_uart=1" "$CONFIG_FILE"; then
        echo "enable_uart=1" | sudo tee -a "$CONFIG_FILE"
        echo "Added enable_uart=1 to config"
    else
        echo "UART already enabled in config"
    fi
    
    # Optionally disable Bluetooth to free up UART (ask user)
    echo ""
    read -p "Disable Bluetooth to free up UART for reliable serial? (y/N): " DISABLE_BT
    if [[ "$DISABLE_BT" =~ ^[Yy]$ ]]; then
        if ! grep -q "dtoverlay=disable-bt" "$CONFIG_FILE"; then
            echo "dtoverlay=disable-bt" | sudo tee -a "$CONFIG_FILE"
            echo "Added disable-bt overlay to config"
        else
            echo "Bluetooth already disabled in config"
        fi
    fi
    
    # Check for Pi 5 specific settings
    PI_MODEL=$(cat /proc/device-tree/model 2>/dev/null || echo "Unknown")
    if [[ "$PI_MODEL" == *"Raspberry Pi 5"* ]]; then
        echo "Detected Raspberry Pi 5 - applying specific settings..."
        
        # Pi 5 uses different UART configuration
        if ! grep -q "dtparam=uart0=on" "$CONFIG_FILE"; then
            echo "dtparam=uart0=on" | sudo tee -a "$CONFIG_FILE"
            echo "Added uart0=on for Pi 5"
        fi
        
        echo "Note: On Pi 5, use /dev/ttyAMA0 for UART"
    else
        echo "Detected older Pi model: $PI_MODEL"
        echo "Note: Use /dev/ttyAMA0 or /dev/ttyS0 depending on your model"
    fi
    
    # Disable console on serial (works for all Pi versions)
    echo "Disabling serial console..."
    sudo systemctl disable serial-getty@ttyAMA0.service 2>/dev/null || true
    sudo systemctl disable serial-getty@ttyS0.service 2>/dev/null || true
    
    # Check cmdline.txt location and remove console
    CMDLINE_FILE=""
    if [[ -f /boot/firmware/cmdline.txt ]]; then
        CMDLINE_FILE="/boot/firmware/cmdline.txt"
    elif [[ -f /boot/cmdline.txt ]]; then
        CMDLINE_FILE="/boot/cmdline.txt"
    fi
    
    if [[ -n "$CMDLINE_FILE" ]]; then
        echo "Updating $CMDLINE_FILE to disable serial console..."
        # Backup original
        sudo cp "$CMDLINE_FILE" "${CMDLINE_FILE}.backup"
        # Remove console=serial parameters
        sudo sed -i 's/console=serial[^ ]* //g' "$CMDLINE_FILE"
        sudo sed -i 's/console=ttyAMA[^ ]* //g' "$CMDLINE_FILE"
        sudo sed -i 's/console=ttyS[^ ]* //g' "$CMDLINE_FILE"
        echo "Serial console disabled in cmdline.txt"
    fi
    
    echo ""
    echo "Raspberry Pi configuration complete!"
    echo "Changes made:"
    echo "- Enabled UART (enable_uart=1)"
    if [[ "$DISABLE_BT" =~ ^[Yy]$ ]]; then
        echo "- Disabled Bluetooth (dtoverlay=disable-bt)"
    fi
    if [[ "$PI_MODEL" == *"Raspberry Pi 5"* ]]; then
        echo "- Enabled UART0 for Pi 5 (dtparam=uart0=on)"
    fi
    echo "- Disabled serial console services"
    echo "- Updated cmdline.txt to remove serial console"
    echo ""
    echo "IMPORTANT: Please reboot before using PiRemote!"
    echo ""
else
    echo "Not running on Raspberry Pi, skipping Pi-specific configuration"
fi

echo ""
echo "Installation complete!"
echo ""
echo "Configuration Summary:"
echo "======================"
echo "Mode: $MODE"
echo "Config file: /etc/piremote/piremote.conf"
echo "Log files: /var/log/piremote*.log"
if [[ -n "$CONFIG_FILE" ]]; then
    echo "Pi config: $CONFIG_FILE"
fi
echo ""
echo "Next steps:"
echo "1. Edit /etc/piremote/piremote.conf with your settings:"
echo "   - Update TRX_LIST with your server addresses"
echo "   - Set correct server_ip in [audio] section"
echo "   - Configure serial port settings if needed"
echo "   - Set audio device indices (run: python3 -c 'from audio import list_audio_devices; list_audio_devices()')"
if [[ -n "$CONFIG_FILE" ]]; then
    echo "2. REBOOT the system to apply Raspberry Pi configuration changes"
    echo "3. After reboot, start the services"
else
    echo "2. Start the services"
fi
echo ""

if [[ "$MODE" == "server" || "$MODE" == "both" ]]; then
    echo "To start server: sudo systemctl start piremote-server"
    echo "To check server status: sudo systemctl status piremote-server"
    echo "To view server logs: sudo journalctl -u piremote-server -f"
fi

if [[ "$MODE" == "client" || "$MODE" == "both" ]]; then
    echo "To start client: sudo systemctl start piremote-client"
    echo "To check client status: sudo systemctl status piremote-client"
    echo "To view client logs: sudo journalctl -u piremote-client -f"
fi

echo ""
echo "Manual testing:"
if [[ "$MODE" == "server" || "$MODE" == "both" ]]; then
    echo "Server: sudo -u piremote python3 /etc/piremote/server.py"
fi
if [[ "$MODE" == "client" || "$MODE" == "both" ]]; then
    echo "Client: sudo -u piremote python3 /etc/piremote/client.py"
fi
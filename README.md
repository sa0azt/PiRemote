# PiRemote

A Raspberry Pi-based system for remotely extending commercial radio systems like the Simoco SRM9000. Separates the front panel from the radio unit over IP networks with full duplex audio and serial control.

![PiRemote Architecture](https://img.shields.io/badge/Architecture-Client%2FServer-blue) ![Platform](https://img.shields.io/badge/Platform-Raspberry%20Pi-red) ![Audio](https://img.shields.io/badge/Audio-Opus%20Codec-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

## Features

- **Full Duplex Audio** - Simultaneous TX/RX audio using Opus codec compression
- **Serial Control Bridge** - TCP bridge for radio control data with automatic failover
- **GPIO Power Management** - Hardware power button control with status LED
- **Low Latency** - Optimized for real-time radio communications

## 📡 Architecture

```
┌─────────────────┐    IP Network    ┌─────────────────┐
│   Front Panel   │◄────────────────►│   Radio Unit    │
│   (Client)      │                  │   (Server)      │
├─────────────────┤                  ├─────────────────┤
│ • GPIO Control  │                  │ • Serial Bridge │
│ • Audio I/O     │                  │ • Audio I/O     │
│ • Serial Data   │                  │ • Radio Control │
└─────────────────┘                  └─────────────────┘
```

### Communication Channels:
- **Port 5000**: Serial control data (TCP)
- **Port 5001**: TX audio - microphone to radio (UDP/Opus)
- **Port 5002**: RX audio - radio to speaker (UDP/Opus)

## Quick Start

### 1. Clone Repository
```bash
git clone https://github.com/yourusername/piremote.git
cd piremote
```

### 2. Run Installation Script
```bash
chmod +x install.sh
./install.sh
```

Choose installation type:
- **Server** (radio end)
- **Client** (front panel end)  
- **Both** (development/testing)

### 3. Configure System
```bash
sudo nano /etc/piremote/piremote.conf
```

Update key settings:
```ini
[CLIENT]
TRX_LIST=radio1.example.com:5000,radio2.example.com:5000

[audio]
server_ip=192.168.1.100
tx_port=5001
rx_port=5002
```

### 4. Start Services
```bash
# Server side
sudo systemctl start piremote-server

# Client side  
sudo systemctl start piremote-client
```

## Hardware Setup

### Client Side (Front Panel)
```
Raspberry Pi GPIO Connections:
├── GPIO 27 → Power Control Output
├── GPIO 17 → Power Button Input (pull-up)
├── UART → Radio Control Interface
└── Audio → USB Sound Card or Pi Audio
```

### Server Side (Radio Unit)
```
Raspberry Pi Connections:
├── UART → Radio Serial Interface
├── Audio In → Radio Receiver Audio
├── Audio Out → Radio Transmitter Audio
└── Network → Ethernet/WiFi Connection
```

### Supported Radios
- **Simoco SRM9000**
- **Ericsson Aurora (Coming soon)**

## Audio Configuration

### Configure Devices
```ini
[audio]
# Client side (front panel)
input_device=1     # Microphone
output_device=0    # Speaker

# Server side (radio)  
input_device=2     # Radio RX audio
output_device=1    # Radio TX audio
```

### Audio Specifications
- **Codec**: Opus (efficient compression)
- **Sample Rate**: 48kHz
- **Channels**: Mono (1 channel)
- **Frame Size**: 960 samples (20ms)
- **Latency**: ~40ms typical

## Configuration Reference

### Complete Configuration Example
```ini
[MAIN]
TYPE=CLIENT
DEBUG=False
LOG_LEVEL=INFO

[CLIENT]
SERIAL_PORT=/dev/ttyAMA0
SERIAL_BAUD=19200
TRX_LIST=radio1.local:5000,radio2.local:5000,10.0.1.100:5000

[SERVER] 
SERIAL_PORT=/dev/ttyAMA0
SERIAL_BAUD=19200
LISTEN_PORT=5000

[serial]
port=/dev/ttyAMA0
baudrate=19200

[radio]
listen_ip=0.0.0.0
listen_port=5000

[audio]
server_ip=192.168.1.100
tx_port=5001
rx_port=5002
sample_rate=48000
channels=1
frame_size=960
input_device=
output_device=

[logging]
level=INFO
```

## System Management

### Service Commands
```bash
# Status
sudo systemctl status piremote-server
sudo systemctl status piremote-client

# Logs
sudo journalctl -u piremote-server -f
sudo journalctl -u piremote-client -f

# Manual Testing
sudo -u piremote python3 /etc/piremote/server.py
sudo -u piremote python3 /etc/piremote/client.py
```

### Log Files
- `/var/log/piremote-server.log`
- `/var/log/piremote-client.log`
- `/var/log/piremote-*-error.log`

## Raspberry Pi Setup

### Automatic Configuration
The install script handles Pi configuration automatically:

**All Pi Models:**
- Enables UART (`enable_uart=1`)
- Disables serial console
- Configures GPIO permissions

**Raspberry Pi 5:**
- Enables UART0 (`dtparam=uart0=on`)
- Uses `/dev/ttyAMA0`

**Legacy Models:**
- Standard UART configuration
- May use `/dev/ttyS0` or `/dev/ttyAMA0`

### Manual Pi Configuration
```bash
# Enable UART
echo "enable_uart=1" | sudo tee -a /boot/config.txt

# Disable Bluetooth (optional, frees UART)
echo "dtoverlay=disable-bt" | sudo tee -a /boot/config.txt

# Disable console
sudo systemctl disable serial-getty@ttyAMA0.service
```

## Troubleshooting

### Common Issues

**Serial Permission Denied**
```bash
sudo usermod -a -G dialout piremote
sudo chmod 666 /dev/ttyAMA0
```

**Audio Device Not Found**
```bash
# List devices
python3 -c "from audio import list_audio_devices; list_audio_devices()"

# Test audio
aplay -l  # List playback devices
arecord -l  # List capture devices
```

**GPIO Permission Denied**
```bash
sudo usermod -a -G gpio piremote
```

**Network Connectivity**
```bash
# Test TCP connection
telnet radio_server 5000

# Test UDP (audio)
nc -u radio_server 5001
```

### Debug Mode
Enable verbose logging:
```ini
[MAIN]
DEBUG=True
LOG_LEVEL=DEBUG
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
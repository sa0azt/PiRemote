# PiRemote

A Raspberry Pi-based system for remotely extending commercial radio systems like the Simoco SRM9000. Separates the front panel from the radio unit over IP networks with full duplex audio and serial control.

![PiRemote Architecture](https://img.shields.io/badge/Architecture-Client%2FServer-blue) ![Platform](https://img.shields.io/badge/Platform-Raspberry%20Pi-red) ![Audio](https://img.shields.io/badge/Audio-Opus%20Codec-green) ![License](https://img.shields.io/badge/License-GPL%203.0-yellow)

## Features

- **Full Duplex Audio** - Simultaneous TX/RX audio using Opus codec compression
- **Serial Control Bridge** - TCP bridge for radio control data with automatic failover
- **GPIO Power Management** - Hardware power button control with status LED
- **Low Latency** - Optimized for real-time radio communications

## Architecture

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
SERVER_IP=192.168.1.100
AUDIO_TX_PORT=5001
AUDIO_RX_PORT=5002
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
[CLIENT]
# Client side (front panel)
AUDIO_INPUT_DEVICE=1     # Microphone
AUDIO_OUTPUT_DEVICE=0    # Speaker

[SERVER]
# Server side (radio)  
AUDIO_INPUT_DEVICE=2     # Radio RX audio
AUDIO_OUTPUT_DEVICE=1    # Radio TX audio
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
# ============================================================================
# PiRemote Configuration File
# ============================================================================
[MAIN]
TYPE=CLIENT                 # Set to CLIENT or SERVER depending on deployment
DEBUG=False
LOG_LEVEL=INFO              # Log level: DEBUG, INFO, WARNING, ERROR
SAMPLE_RATE=48000           # Audio sample rate (Hz)
CHANNELS=1                  # Number of audio channels (1 = mono)
FRAME_SIZE=960              # Audio frame size (samples per packet)

# ============================================================================
# CLIENT CONFIGURATION (Front Panel Side)
# ============================================================================
[CLIENT]
SERIAL_PORT=/dev/ttyAMA0
SERIAL_BAUD=19200
TRX_LIST=radio1.example.com:5000,radio2.example.com:5000
GPIO_POWER_PIN=27                # Power control output pin
GPIO_PWRBUTTON_PIN=17            # Power button input pin (with pull-up)
SERVER_IP=192.168.1.100          # IP address of radio server
AUDIO_TX_PORT=5001               # Send microphone audio to server
AUDIO_RX_PORT=5002               # Receive radio audio from server
AUDIO_INPUT_DEVICE=              # Microphone device index (empty = default)
AUDIO_OUTPUT_DEVICE=             # Speaker device index (empty = default)

# ============================================================================
# SERVER CONFIGURATION (Radio End)
# ============================================================================
[SERVER]
SERIAL_PORT=/dev/ttyAMA0
SERIAL_BAUD=19200
TCP_PORT=5000                    # TCP port for serial bridge
AUDIO_TX_PORT=5001               # Receive microphone audio from clients
AUDIO_RX_PORT=5002               # Send radio audio to clients
AUDIO_INPUT_DEVICE=              # Radio RX audio input (empty = default)
AUDIO_OUTPUT_DEVICE=             # Radio TX audio output (empty = default)
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

This project is licensed under the GPL-3.0 License - see the [LICENSE](LICENSE) file for details.

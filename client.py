#!/usr/bin/env python3
import argparse
import configparser
import logging
import signal
import sys
import socket
import threading
import select
import time
import serial
import serial.threaded
import RPi.GPIO as GPIO
from audio import AudioClient

# GPIO pins
io_pwr = 27            # Power control pin
io_button = 17         # Power toggle button pin

# Globals for service instances and state
serial_proto = None
ser = None
reader = None
radio_bridge = None
audio_client = None
powered_on = False
trx_list = []
current_trx_index = 0
debug = False
cfg = None

# Set up GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(io_pwr, GPIO.OUT)
GPIO.setup(io_button, GPIO.IN, pull_up_down=GPIO.PUD_UP)

class SerialToNet(serial.threaded.Protocol):
    """Forward serial data to the current TCP radio socket."""
    def __init__(self, debug=False):
        self.socket = None
        self.debug = debug
        self.lock = threading.Lock()

    def __call__(self):
        return self

    def data_received(self, data):
        if self.debug:
            logging.debug(f"Serial received: {data!r}")
        with self.lock:
            if self.socket:
                try:
                    self.socket.sendall(data)
                except Exception as e:
                    logging.error(f"Error sending serial data over radio: {e}")
                    self.socket = None

    def set_socket(self, socket):
        with self.lock:
            self.socket = socket

    def clear_socket(self):
        with self.lock:
            self.socket = None

class RadioBridge(threading.Thread):
    """Serial-TCP radio bridge client with failover support."""
    def __init__(self, trx_list, ser, ser_proto, debug=False):
        super().__init__(daemon=True)
        self.trx_list = trx_list
        self.ser = ser
        self.ser_proto = ser_proto
        self.debug = debug
        self.stop_event = threading.Event()
        self.current_trx_index = 0

    def run(self):
        while not self.stop_event.is_set():
            # Get current TRX from list
            if not self.trx_list:
                logging.error("No TRX servers configured")
                break
                
            trx = self.trx_list[self.current_trx_index]
            try:
                host, port = trx.split(':')
                port = int(port)
            except ValueError:
                logging.error(f"Invalid TRX format: {trx}")
                self._next_trx()
                continue

            logging.info(f"Connecting to radio {host}:{port}...")
            
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(5.0)  # Connection timeout
                    sock.connect((host, port))
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    sock.settimeout(1.0)  # Data timeout
                    
                    logging.info(f"Radio bridge connected to {host}:{port}")
                    self.ser_proto.set_socket(sock)
                    
                    # Reset TRX index on successful connection
                    self.current_trx_index = 0
                    
                    # Handle data exchange
                    while not self.stop_event.is_set():
                        try:
                            ready, _, _ = select.select([sock], [], [], 0.5)
                            if sock in ready:
                                data = sock.recv(1024)
                                if not data:
                                    logging.warning("Radio connection closed by server")
                                    break
                                if self.ser and self.ser.is_open:
                                    self.ser.write(data)
                                else:
                                    logging.warning("Serial port not available")
                        except socket.timeout:
                            continue
                        except Exception as e:
                            logging.error(f"Radio data exchange error: {e}")
                            break
                            
            except (socket.timeout, ConnectionRefusedError, OSError) as e:
                logging.warning(f"Failed to connect to {host}:{port}: {e}")
                self._next_trx()
                if not self.stop_event.is_set():
                    time.sleep(2)  # Wait before trying next server
            except Exception as e:
                logging.error(f"Radio bridge error: {e}")
                if not self.stop_event.is_set():
                    time.sleep(1)
            finally:
                self.ser_proto.clear_socket()
                logging.info("Radio bridge disconnected")

    def _next_trx(self):
        """Move to next TRX in list for failover."""
        self.current_trx_index = (self.current_trx_index + 1) % len(self.trx_list)
        if self.current_trx_index == 0:
            logging.info("Cycled through all TRX servers, starting over")

    def stop(self):
        self.stop_event.set()
        self.join(timeout=5.0)

# Power control function
def Pwr(state: bool):
    GPIO.output(io_pwr, GPIO.HIGH if state else GPIO.LOW)
    logging.info(f"Power {'ON' if state else 'OFF'}")

# Helper to properly close serial connection
def ser_close():
    global ser, reader, serial_proto
    
    if reader:
        try:
            reader.stop()
        except:
            pass
        reader = None
        
    if ser and ser.is_open:
        try:
            ser.close()
        except:
            pass
        ser = None
        
    serial_proto = None

# Helper to open serial port and start reader
def ser_open():
    global ser, serial_proto, reader
    
    # Close any existing connection first
    ser_close()
    
    serial_port = cfg.get('CLIENT', 'SERIAL_PORT')
    serial_baud = cfg.getint('CLIENT', 'SERIAL_BAUD')
    
    try:
        ser = serial.serial_for_url(serial_port, do_not_open=True)
        ser.baudrate = serial_baud
        ser.bytesize = 8
        ser.parity = 'N'
        ser.stopbits = 1
        ser.rtscts = True
        ser.xonxoff = False
        ser.open()
        
        logging.info(f"Serial port {serial_port} opened at {serial_baud} baud")
        
        serial_proto = SerialToNet(debug)
        reader = serial.threaded.ReaderThread(ser, serial_proto)
        reader.start()
        
        return True
    except Exception as e:
        logging.error(f"Cannot open serial port: {e}")
        ser_close()
        return False

# Toggle all services when the power button is pressed
def toggle_power(channel=None):
    global powered_on, radio_bridge, audio_client
    
    if not powered_on:
        # Power on: setup serial, start services
        logging.info("Powering on...")
        Pwr(True)
        
        # Setup serial connection
        if not ser_open():
            logging.error("Failed to open serial port, aborting power on")
            Pwr(False)
            return
            
        try:
            # Start radio bridge with failover support
            radio_bridge = RadioBridge(trx_list, ser, serial_proto, debug)
            radio_bridge.start()
            
            # Start audio client (full duplex)
            audio_client = AudioClient(cfg)
            audio_client.start()
            
            powered_on = True
            logging.info("System powered on successfully")
            
        except Exception as e:
            logging.error(f"Failed to start services: {e}")
            # Cleanup on failure
            if radio_bridge:
                radio_bridge.stop()
                radio_bridge = None
            if audio_client:
                audio_client.stop()
                audio_client = None
            ser_close()
            Pwr(False)
    else:
        # Power off: stop services and power down
        logging.info("Powering off...")
        
        if radio_bridge:
            radio_bridge.stop()
            radio_bridge = None
            
        if audio_client:
            audio_client.stop()
            audio_client = None
            
        ser_close()
        Pwr(False)
        powered_on = False
        logging.info("System powered off")

def cleanup_and_exit():
    """Clean shutdown of all resources."""
    global powered_on
    
    logging.info("Cleaning up...")
    
    # Remove GPIO event detection
    try:
        GPIO.remove_event_detect(io_button)
    except:
        pass
        
    # Power off if needed
    if powered_on:
        toggle_power()
        
    # GPIO cleanup
    try:
        GPIO.cleanup()
    except:
        pass
    
    logging.info("Cleanup complete")

def signal_handler(signum, frame):
    logging.info(f"Received signal {signum}")
    cleanup_and_exit()
    sys.exit(0)

# Main entry point
def main():
    global cfg, trx_list, debug
    
    parser = argparse.ArgumentParser(description="PiRemote client")
    parser.add_argument('-c', '--config', default='/etc/piremote/piremote.conf', 
                       help='Path to config file')
    args = parser.parse_args()

    # Load config
    cfg = configparser.ConfigParser(
        converters={'list': lambda x: [i.strip() for i in x.split(',')]}
    )
    
    try:
        cfg.read(args.config)
    except Exception as e:
        print(f"Failed to read config file {args.config}: {e}")
        sys.exit(1)

    # Validate required sections
    required_sections = ['MAIN', 'CLIENT', 'audio']
    for section in required_sections:
        if section not in cfg:
            print(f"Missing required section [{section}] in config")
            sys.exit(1)

    try:
        trx_list = cfg.getlist('CLIENT', 'TRX_LIST')
        if not trx_list:
            raise ValueError("TRX_LIST cannot be empty")
    except Exception as e:
        print(f"Configuration error: {e}")
        sys.exit(1)

    # Logging setup
    debug = cfg.getboolean('MAIN', 'DEBUG', fallback=False)
    level = 'DEBUG' if debug else cfg.get('MAIN', 'LOG_LEVEL', fallback='INFO').upper()
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s: %(message)s"
    )

    logging.info("PiRemote Client starting...")
    logging.info(f"Configured TRX servers: {trx_list}")

    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Set up button event for toggling power
        GPIO.add_event_detect(io_button, GPIO.FALLING, 
                             callback=toggle_power, bouncetime=500)

        # Initial state: ensure power off
        Pwr(False)
        logging.info("Ready. Press power button to start...")

        # Block indefinitely
        signal.pause()
        
    except Exception as e:
        logging.error(f"Fatal error: {e}")
    finally:
        cleanup_and_exit()

if __name__ == "__main__":
    main()
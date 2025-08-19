#!/usr/bin/env python3

import signal
import sys
import socket
import threading
import serial
import serial.threaded
import configparser
import logging

from audio import AudioServer

class SerialToNet(serial.threaded.Protocol):
    def __init__(self):
        self.socket = None
        self.lock = threading.Lock()

    def __call__(self):
        return self

    def data_received(self, data):
        with self.lock:
            if self.socket:
                try:
                    self.socket.sendall(data)
                except Exception as e:
                    logging.error(f"Error sending serial data over TCP: {e}")
                    self.socket = None

    def set_socket(self, socket):
        with self.lock:
            self.socket = socket

    def clear_socket(self):
        with self.lock:
            self.socket = None

def radio_bridge(listen_port, serial_proto, stop_event):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.settimeout(1.0)
    
    try:
        srv.bind(('0.0.0.0', listen_port))
        srv.listen(1)
        logging.info(f"Radio bridge listening on 0.0.0.0:{listen_port}")

        while not stop_event.is_set():
            try:
                client, addr = srv.accept()
                logging.info(f"Serial client connected from {addr}")
                
                for opt in [
                    (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
                    (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                ]:
                    try:
                        client.setsockopt(*opt)
                    except Exception:
                        pass

                client.settimeout(1.0)
                serial_proto.set_socket(client)
                
                while not stop_event.is_set():
                    try:
                        data = client.recv(1024)
                        if not data:
                            break
                        if hasattr(serial_proto.transport, 'write'):
                            serial_proto.transport.write(data)
                        else:
                            logging.warning("Serial transport not available")
                    except socket.timeout:
                        continue
                    except Exception as e:
                        logging.error(f"Error receiving client data: {e}")
                        break
                        
            except socket.timeout:
                continue
            except Exception as e:
                if not stop_event.is_set():
                    logging.error(f"Radio bridge error: {e}")
            finally:
                serial_proto.clear_socket()
                try:
                    client.close()
                except:
                    pass
                logging.info("Serial client disconnected")

    finally:
        srv.close()
        logging.info("Radio bridge server closed")

def signal_handler(signum, frame):
    global stop_event, audio_server, reader
    logging.info(f"Received signal {signum}, shutting down...")
    stop_event.set()
    
    if audio_server:
        audio_server.stop()
    
    if reader:
        reader.stop()

def main():
    global stop_event, audio_server, reader
    
    cfg = configparser.ConfigParser()
    try:
        cfg.read("/etc/piremote/piremote.conf")
    except Exception as e:
        logging.error(f"Failed to read config: {e}")
        sys.exit(1)

    log_level = cfg.get("MAIN", "LOG_LEVEL", fallback="INFO")
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s %(levelname)s: %(message)s"
    )
    logging.info("PiRemote Server starting...")

    try:
        serial_port = cfg.get("SERVER", "SERIAL_PORT")
        serial_baud = cfg.getint("SERVER", "SERIAL_BAUD")
        tcp_port = cfg.getint("SERVER", "TCP_PORT")
    except Exception as e:
        logging.error(f"Configuration error: {e}")
        sys.exit(1)

    stop_event = threading.Event()
    audio_server = None
    reader = None

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        audio_server = AudioServer(config_path="/etc/piremote/piremote.conf")
        audio_server.start()

        ser = serial.serial_for_url(serial_port, do_not_open=True)
        ser.baudrate = serial_baud
        ser.bytesize = 8
        ser.parity = 'N'
        ser.stopbits = 1
        ser.rtscts = True
        ser.xonxoff = False

        try:
            ser.open()
            logging.info(f"Serial port {serial_port} opened at {serial_baud} baud")
        except serial.SerialException as e:
            logging.error(f"Could not open serial port {serial_port}: {e}")
            sys.exit(1)

        serial_proto = SerialToNet()
        reader = serial.threaded.ReaderThread(ser, serial_proto)
        reader.start()

        radio_thread = threading.Thread(
            target=radio_bridge,
            args=(tcp_port, serial_proto, stop_event),
            daemon=True
        )
        radio_thread.start()

        logging.info("All services started successfully")
        
        try:
            while not stop_event.is_set():
                stop_event.wait(1.0)
        except KeyboardInterrupt:
            logging.info("Keyboard interrupt received")

    except Exception as e:
        logging.error(f"Fatal error: {e}")
    finally:
        logging.info("Shutting down...")
        stop_event.set()
        
        if audio_server:
            audio_server.stop()
        
        if reader:
            reader.stop()
            
        if 'ser' in locals():
            ser.close()
            
        logging.info("Shutdown complete")

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
import signal
import sys
import socket
import threading
import serial
import serial.threaded
import configparser
import logging
from opuslib import Decoder

class SerialToNet(serial.threaded.Protocol):
    """Forward serial data to the current TCP client socket."""
    def __init__(self):
        self.socket = None

    def __call__(self):
        return self

    def data_received(self, data):
        if self.socket:
            try:
                self.socket.sendall(data)
            except Exception as e:
                logging.error(f"Error sending serial data over TCP: {e}")

class RadioBridge(threading.Thread):
    """Serial-to-TCP bridge server."""
    def __init__(self, listen_ip, listen_port, ser_proto, stop_event):
        super().__init__(daemon=True)
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        self.ser_proto = ser_proto
        self.stop_event = stop_event
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    def run(self):
        self.sock.bind((self.listen_ip, self.listen_port))
        self.sock.listen(1)
        logging.info(f"Radio bridge listening on {self.listen_ip}:{self.listen_port}")
        while not self.stop_event.is_set():
            try:
                client, addr = self.sock.accept()
                logging.info(f"Serial client connected from {addr}")
                for opt in [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
                            (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)]:
                    try:
                        client.setsockopt(*opt)
                    except Exception:
                        pass
                self.ser_proto.socket = client
                while not self.stop_event.is_set():
                    data = client.recv(1024)
                    if not data:
                        break
                    self.ser_proto.transport.write(data)
            except Exception as e:
                if not self.stop_event.is_set():
                    logging.error(f"Radio bridge error: {e}")
            finally:
                self.ser_proto.socket = None
                try:
                    client.close()
                except:
                    pass
                logging.info("Serial client disconnected")
        self.sock.close()
        logging.info("Radio bridge stopped")

class AudioServer:
    """UDP Opus stream receiver."""
    def __init__(self, cfg):
        sec = cfg['audio']
        self.host = sec.get('listen_ip', fallback='0.0.0.0')
        self.port = sec.getint('listen_port', fallback=5001)
        self.sample_rate = sec.getint('sample_rate', fallback=48000)
        self.channels = sec.getint('channels', fallback=2)
        self.frame_size = sec.getint('frame_size', fallback=960)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.host, self.port))
        logging.info(f"AudioServer listening on {self.host}:{self.port}")

        self.decoder = Decoder(self.sample_rate, self.channels)
        self._thread = None
        self._running = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()
        logging.info("Audio reception thread started")

    def _recv_loop(self):
        while self._running:
            try:
                data, _ = self.sock.recvfrom(4096)
                pcm = self.decoder.decode(data, self.frame_size)
                self.handle_pcm(pcm)
            except Exception as e:
                logging.error(f"Error in audio recv loop: {e}")

    def handle_pcm(self, pcm_bytes):
        """Override to process or play PCM frames."""
        logging.debug(f"Received {len(pcm_bytes)} bytes of PCM audio")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join()
        self.sock.close()
        logging.info("AudioServer stopped")

def main():
    # Load configuration
    cfg = configparser.ConfigParser()
    cfg.read('server.conf')

    # Logging setup
    level = cfg.get('logging', 'level', fallback='INFO').upper()
    logging.basicConfig(level=level, format='%(asctime)s %(levelname)s: %(message)s')

    # Serial setup
    serial_port = cfg.get('serial', 'port')
    serial_baud = cfg.getint('serial', 'baudrate')
    ser = serial.serial_for_url(serial_port, do_not_open=True)
    ser.baudrate = serial_baud
    ser.bytesize = 8
    ser.parity = 'N'
    ser.stopbits = 1
    ser.rtscts = True
    ser.xonxoff = False
    try:
        ser.open()
    except serial.SerialException as e:
        logging.error(f"Could not open serial port {serial_port}: {e}")
        sys.exit(1)

    serial_proto = SerialToNet()
    reader = serial.threaded.ReaderThread(ser, serial_proto)
    reader.start()

    # Radio bridge
    stop_event = threading.Event()
    rb = RadioBridge(
        cfg.get('radio', 'listen_ip', fallback='0.0.0.0'),
        cfg.getint('radio', 'listen_port'),
        serial_proto,
        stop_event
    )
    rb.start()

    # Audio server
    audio_server = AudioServer(cfg)
    audio_server.start()

    # Graceful shutdown
    def shutdown(sig, frame):
        logging.info("Shutting down...")
        stop_event.set()
        rb.join()
        audio_server.stop()
        reader.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Keep running
    signal.pause()

if __name__ == '__main__':
    main()

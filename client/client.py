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
import pyaudio
from opuslib import Encoder, APPLICATION_AUDIO

# GPIO pins
io_pwr = 27
io_button = 17

# Globals
cfg = None
ser = None
serial_proto = None
radio_bridge = None
audio_client = None
powered_on = False
trx_list = []
debug = False

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(io_pwr, GPIO.OUT)
GPIO.setup(io_button, GPIO.IN, pull_up_down=GPIO.PUD_UP)

class SerialToNet(serial.threaded.Protocol):
    def __init__(self, debug=False):
        self.socket = None
        self.debug = debug

    def __call__(self):
        return self

    def data_received(self, data):
        if self.debug:
            logging.debug(f"Serial received: {data!r}")
        if self.socket:
            try:
                self.socket.sendall(data)
            except Exception as e:
                logging.error(f"Error sending serial data over radio: {e}")

class RadioBridge(threading.Thread):
    def __init__(self, host, port, ser_inst, ser_proto, debug=False):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.ser = ser_inst
        self.ser_proto = ser_proto
        self.debug = debug
        self.stop_event = threading.Event()

    def run(self):
        while not self.stop_event.is_set():
            logging.info(f"Connecting to radio {self.host}:{self.port}...")
            with socket.socket() as sock:
                try:
                    sock.connect((self.host, self.port))
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    logging.info("Radio bridge connected")
                    self.ser_proto.socket = sock
                    while not self.stop_event.is_set():
                        ready, _, _ = select.select([sock], [], [], 0.5)
                        if sock in ready:
                            data = sock.recv(1024)
                            if not data:
                                break
                            self.ser.write(data)
                except Exception as e:
                    logging.error(f"Radio bridge error: {e}")
                    time.sleep(1)
                finally:
                    self.ser_proto.socket = None
                    logging.info("Radio bridge disconnected")

    def stop(self):
        self.stop_event.set()

class AudioClient:
    def __init__(self, config):
        sec = config['audio']
        self.server_ip = sec.get('server_ip')
        self.server_port = sec.getint('server_port')
        self.sample_rate = sec.getint('sample_rate', fallback=48000)
        self.channels = sec.getint('channels', fallback=1)
        self.frame_size = sec.getint('frame_size', fallback=960)

        self.encoder = Encoder(self.sample_rate, self.channels, APPLICATION_AUDIO)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.p = pyaudio.PyAudio()
        self.stream = self.p.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=self.frame_size
        )
        self.thread = None
        self.running = False

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._send_loop, daemon=True)
        self.thread.start()
        logging.info("Audio client started")

    def _send_loop(self):
        while self.running:
            pcm = self.stream.read(self.frame_size, exception_on_overflow=False)
            packet = self.encoder.encode(pcm, self.frame_size)
            try:
                self.sock.sendto(packet, (self.server_ip, self.server_port))
            except Exception as e:
                logging.error(f"Audio send error: {e}")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()
        self.stream.stop_stream()
        self.stream.close()
        self.p.terminate()
        self.sock.close()
        logging.info("Audio client stopped")

def Pwr(state):
    GPIO.output(io_pwr, GPIO.HIGH if state else GPIO.LOW)
    logging.info(f"Power {'ON' if state else 'OFF'}")

def toggle_power(channel=None):
    global powered_on, ser, serial_proto, radio_bridge, audio_client
    if not powered_on:
        Pwr(True)
        serial_port = cfg.get('serial','port')
        serial_baud = cfg.getint('serial','baudrate')
        ser = serial.serial_for_url(serial_port, do_not_open=True)
        ser.baudrate = serial_baud
        ser.bytesize = 8
        ser.parity = 'N'
        ser.stopbits = 1
        ser.rtscts = True
        ser.xonxoff = False
        try:
            ser.open()
        except Exception as e:
            logging.error(f"Cannot open serial port: {e}")
            return
        serial_proto = SerialToNet(debug)
        reader = serial.threaded.ReaderThread(ser, serial_proto)
        reader.start()
        trx_list = [e.strip() for e in cfg.get('radio','list').split(',')]
        host, port = trx_list[0].split(':')
        radio_bridge = RadioBridge(host,int(port),ser,serial_proto,debug)
        radio_bridge.start()
        audio_client = AudioClient(cfg)
        audio_client.start()
        powered_on = True
    else:
        if radio_bridge: radio_bridge.stop()
        if audio_client: audio_client.stop()
        if ser: ser.close()
        Pwr(False)
        powered_on = False

def main():
    global cfg, trx_list, debug
    parser = argparse.ArgumentParser(description="PiRemote client")
    parser.add_argument('-c','--config',default='client.conf')
    args = parser.parse_args()
    cfg = configparser.ConfigParser()
    cfg.read(args.config)
    trx_list = [e.strip() for e in cfg.get('radio','list').split(',')]
    level = cfg.get('logging','level',fallback='INFO').upper()
    logging.basicConfig(level=level,format="%(asctime)s %(levelname)s: %(message)s")
    debug = (level == 'DEBUG')
    GPIO.add_event_detect(io_button,GPIO.FALLING,callback=toggle_power,bouncetime=500)
    signal.signal(signal.SIGINT,lambda *_) : sys.exit(0)
    signal.signal(signal.SIGTERM,lambda *_) : sys.exit(0)
    Pwr(False)
    logging.info("Awaiting power button press...")
    signal.pause()

if __name__=="__main__":
    main()

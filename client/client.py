#!/usr/bin/env python3
"""
PiRemote Client - Bridges serial, network, and audio streaming with clean shutdown and minimal logging noise.
"""

import argparse
import configparser
import logging
import os
import signal
import socket
import sys
import threading
import select
import time
from typing import Optional

import ctypes
import serial
import serial.threaded
import RPi.GPIO as GPIO
from opuslib import Encoder, APPLICATION_AUDIO

#------------------------------------------------------------------------------
# Silence ALSA and JACK loggers
#------------------------------------------------------------------------------

def silence_alsa() -> None:
    """Disable ALSA's built-in error printing."""
    try:
        def _alsa_err(_file, _line, _func, _err, _fmt): pass
        CB = ctypes.CFUNCTYPE(
            None,
            ctypes.c_char_p, ctypes.c_int,
            ctypes.c_char_p, ctypes.c_int,
            ctypes.c_char_p
        )(_alsa_err)
        lib = ctypes.cdll.LoadLibrary('libasound.so.2')
        lib.snd_lib_error_set_handler(CB)
    except Exception:
        pass


def silence_jack() -> None:
    """Disable JACK's error/info callbacks."""
    try:
        def _jack_err(_msg): pass
        CB = ctypes.CFUNCTYPE(None, ctypes.c_char_p)(_jack_err)
        lib = ctypes.cdll.LoadLibrary('libjack.so.0')
        lib.jack_set_error_function(CB)
        lib.jack_set_info_function(CB)
    except Exception:
        pass


silence_alsa()
silence_jack()

# Silence PyAudio host probing (JACK/ALSA) by redirecting stderr
_original_stderr = sys.stderr
sys.stderr = open(os.devnull, 'w')
import pyaudio  # noqa: E402
sys.stderr = _original_stderr

#------------------------------------------------------------------------------
# GPIO pin configuration
#------------------------------------------------------------------------------

POWER_PIN = 27
IN1_PIN = 17
IN2_PIN = 22
IN3_PIN = 23
OUT1_PIN = 24
OUT2_PIN = 25
OUT3_PIN = 26
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(POWER_PIN, GPIO.OUT)
GPIO.setup(OUT1_PIN, GPIO.OUT)
GPIO.setup(OUT2_PIN, GPIO.OUT)
GPIO.setup(OUT3_PIN, GPIO.OUT)
GPIO.setup(IN1_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(IN2_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(IN3_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

#------------------------------------------------------------------------------
# Global state
#------------------------------------------------------------------------------

cfg: configparser.ConfigParser
serial_conn: Optional[serial.SerialBase] = None
serial_reader: Optional[serial.threaded.ReaderThread] = None
radio_bridge: Optional[threading.Thread] = None
audio_client: Optional['AudioClient'] = None
powered_on: bool = False
_debug: bool = False

#------------------------------------------------------------------------------
# Serial-to-Network protocol
#------------------------------------------------------------------------------

class SerialToNet(serial.threaded.Protocol):
    """Forward serial bytes to a TCP socket."""

    def __init__(self, debug: bool = False):
        self.socket: Optional[socket.socket] = None
        self.debug = debug

    def __call__(self):
        return self

    def data_received(self, data: bytes) -> None:
        if self.debug:
            logging.debug(f"Serial received: {data!r}")
        if self.socket:
            try:
                self.socket.sendall(data)
            except Exception as exc:
                logging.error(f"Failed to forward serial data: {exc}")

#------------------------------------------------------------------------------
# Radio bridge thread
#------------------------------------------------------------------------------

class RadioBridge(threading.Thread):
    """Bridge data between TCP and serial port."""

    def __init__(self, host: str, port: int, serial_port, protocol: SerialToNet, debug: bool = False):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.serial = serial_port
        self.protocol = protocol
        self.debug = debug
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            logging.info(f"Connecting to {self.host}:{self.port}")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                try:
                    sock.connect((self.host, self.port))
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, True)
                    logging.info("Radio bridge connected")
                    self.protocol.socket = sock
                    while not self._stop_event.is_set():
                        ready, _, _ = select.select([sock], [], [], 0.5)
                        if sock in ready:
                            data = sock.recv(1024)
                            if not data:
                                break
                            self.serial.write(data)
                except Exception as exc:
                    logging.error(f"Radio bridge error: {exc}")
                    time.sleep(1)
                finally:
                    self.protocol.socket = None
                    logging.info("Radio bridge disconnected")

    def stop(self) -> None:
        self._stop_event.set()

#------------------------------------------------------------------------------
# Audio streaming client
#------------------------------------------------------------------------------

class AudioClient:
    """Capture audio, encode with Opus, and send via UDP."""

    def __init__(self, config: configparser.ConfigParser):
        sec = config['audio']
        self.server_addr = (sec['server_ip'], sec.getint('server_port'))
        self.rate = sec.getint('sample_rate', 48000)
        self.channels = sec.getint('channels', 1)
        self.frames = sec.getint('frame_size', 960)

        self.encoder = Encoder(self.rate, self.channels, APPLICATION_AUDIO)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.pa = pyaudio.PyAudio()
        self.stream = self.pa.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.rate,
            input=True,
            frames_per_buffer=self.frames
        )
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._send_loop, daemon=True)
        self._thread.start()
        logging.info("Audio client started")

    def _send_loop(self) -> None:
        while self._running:
            pcm = self.stream.read(self.frames, exception_on_overflow=False)
            packet = self.encoder.encode(pcm, self.frames)
            try:
                self.sock.sendto(packet, self.server_addr)
            except Exception as exc:
                logging.error(f"Audio send error: {exc}")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join()
        self.stream.stop_stream()
        self.stream.close()
        self.pa.terminate()
        self.sock.close()
        logging.info("Audio client stopped")

#------------------------------------------------------------------------------
# Power control and toggle logic
#------------------------------------------------------------------------------

def set_power(on: bool) -> None:
    GPIO.output(POWER_PIN, GPIO.HIGH if on else GPIO.LOW)
    logging.info(f"Power {'ON' if on else 'OFF'}")


def toggle_power(channel=None) -> None:
    """Start or stop serial, radio, and audio subsystems."""
    global powered_on, serial_conn, serial_reader, radio_bridge, audio_client

    if not powered_on:
        set_power(True)
        port = cfg.get('serial', 'port')
        baud = cfg.getint('serial', 'baudrate')
        serial_conn = serial.serial_for_url(port, do_not_open=True)
        serial_conn.baudrate = baud
        serial_conn.bytesize = serial.EIGHTBITS
        serial_conn.parity = serial.PARITY_NONE
        serial_conn.stopbits = serial.STOPBITS_ONE
        serial_conn.rtscts = True
        serial_conn.xonxoff = False
        try:
            serial_conn.open()
        except Exception as e:
            logging.error(f"Cannot open serial port: {e}")
            return

        proto = SerialToNet(_debug)
        serial_reader = serial.threaded.ReaderThread(serial_conn, proto)
        serial_reader.start()

        hosts = [h.strip() for h in cfg.get('radio', 'list').split(',')]
        host, port_str = hosts[0].split(':')
        radio_bridge = RadioBridge(host, int(port_str), serial_conn, proto, _debug)
        radio_bridge.start()

        audio_client = AudioClient(cfg)
        audio_client.start()
        powered_on = True
    else:
        if radio_bridge:
            radio_bridge.stop()
            radio_bridge.join()
        if audio_client:
            audio_client.stop()
        if serial_reader:
            serial_reader.stop()
            serial_reader.join()
        if serial_conn:
            serial_conn.close()
        set_power(False)
        powered_on = False

#------------------------------------------------------------------------------
# Entry point
#------------------------------------------------------------------------------

def main() -> None:
    global cfg, _debug
    parser = argparse.ArgumentParser(description="PiRemote client")
    parser.add_argument('-c', '--config', default='client.conf')
    args = parser.parse_args()

    cfg = configparser.ConfigParser()
    cfg.read(args.config)

    level = cfg.get('logging', 'level', fallback='INFO').upper()
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s: %(message)s")
    _debug = (level == 'DEBUG')

    GPIO.add_event_detect(IN1_PIN, GPIO.FALLING,
                          callback=toggle_power, bouncetime=500)
    signal.signal(signal.SIGINT, lambda *a: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))

    set_power(False)
    logging.info("Awaiting button press...")
    signal.pause()


if __name__ == "__main__":
    main()

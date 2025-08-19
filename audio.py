#!/usr/bin/env python3

import configparser
import logging
import socket
import threading
import time
import pyaudio
from opuslib import Encoder, Decoder, APPLICATION_AUDIO

class AudioClient:
    def __init__(self, cfg):
        if 'CLIENT' not in cfg:
            raise ValueError("Missing [CLIENT] section in configuration")
            
        client_sec = cfg['CLIENT']
        main_sec = cfg['MAIN']
        
        self.server_ip = client_sec.get('SERVER_IP')
        self.tx_port = client_sec.getint('AUDIO_TX_PORT', fallback=5001)
        self.rx_port = client_sec.getint('AUDIO_RX_PORT', fallback=5002)
        self.sample_rate = main_sec.getint('SAMPLE_RATE', fallback=48000)
        self.channels = main_sec.getint('CHANNELS', fallback=1)
        self.frame_size = main_sec.getint('FRAME_SIZE', fallback=960)

        self.input_device = client_sec.getint('AUDIO_INPUT_DEVICE', fallback=None)
        self.output_device = client_sec.getint('AUDIO_OUTPUT_DEVICE', fallback=None)
        
        if not self.server_ip:
            raise ValueError("SERVER_IP not configured in [CLIENT] section")

        self.encoder = Encoder(self.sample_rate, self.channels, APPLICATION_AUDIO)
        self.decoder = Decoder(self.sample_rate, self.channels)
        
        self.tx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx_socket.bind(('0.0.0.0', self.rx_port))

        self.p = pyaudio.PyAudio()
        
        self.input_stream = self.p.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            input_device_index=self.input_device,
            frames_per_buffer=self.frame_size
        )

        self.output_stream = self.p.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.sample_rate,
            output=True,
            output_device_index=self.output_device,
            frames_per_buffer=self.frame_size
        )
        
        self.running = False
        self.tx_thread = None
        self.rx_thread = None

    def start(self):
        self.running = True
        
        self.tx_thread = threading.Thread(target=self._tx_loop, daemon=True)
        self.tx_thread.start()
        
        self.rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self.rx_thread.start()
        
        logging.info(f"Audio started - TX to {self.server_ip}:{self.tx_port}, RX on port {self.rx_port}")

    def _tx_loop(self):
        while self.running:
            try:
                pcm = self.input_stream.read(self.frame_size, exception_on_overflow=False)
                packet = self.encoder.encode(pcm, self.frame_size)
                self.tx_socket.sendto(packet, (self.server_ip, self.tx_port))
            except Exception as e:
                logging.error(f"TX audio error: {e}")
                time.sleep(0.1)

    def _rx_loop(self):
        self.rx_socket.settimeout(1.0)
        
        while self.running:
            try:
                data, addr = self.rx_socket.recvfrom(4096)
                if not data:
                    continue
                
                pcm = self.decoder.decode(data, self.frame_size)
                
                if pcm:
                    self.output_stream.write(pcm, num_frames=self.frame_size)
                    
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    logging.error(f"RX audio error: {e}")
                    time.sleep(0.1)

    def stop(self):
        self.running = False
        
        if self.tx_thread and self.tx_thread.is_alive():
            self.tx_thread.join(timeout=2.0)
        if self.rx_thread and self.rx_thread.is_alive():
            self.rx_thread.join(timeout=2.0)
        
        try:
            self.input_stream.stop_stream()
            self.input_stream.close()
            self.output_stream.stop_stream()
            self.output_stream.close()
            self.p.terminate()
        except:
            pass

        try:
            self.tx_socket.close()
            self.rx_socket.close()
        except:
            pass
        
        logging.info("Audio client stopped")


class AudioServer:
    def __init__(self, config_path="/etc/piremote/piremote.conf"):
        cfg = configparser.ConfigParser()
        cfg.read(config_path)
        
        if 'SERVER' not in cfg:
            raise ValueError("Missing [SERVER] section in configuration")
            
        server_sec = cfg["SERVER"]
        main_sec = cfg["MAIN"]
        
        self.tx_port = server_sec.getint("AUDIO_TX_PORT", fallback=5001)
        self.rx_port = server_sec.getint("AUDIO_RX_PORT", fallback=5002)
        self.sample_rate = main_sec.getint("SAMPLE_RATE", fallback=48000)
        self.channels = main_sec.getint("CHANNELS", fallback=1)
        self.frame_size = main_sec.getint("FRAME_SIZE", fallback=960)
        
        self.input_device = server_sec.getint('AUDIO_INPUT_DEVICE', fallback=None)
        self.output_device = server_sec.getint('AUDIO_OUTPUT_DEVICE', fallback=None)
        
        self.encoder = Encoder(self.sample_rate, self.channels, APPLICATION_AUDIO)
        self.decoder = Decoder(self.sample_rate, self.channels)
        
        self.tx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.tx_socket.bind(('0.0.0.0', self.tx_port))
        
        self.rx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        self.client_address = None
        self.client_lock = threading.Lock()

        self.p = pyaudio.PyAudio()
        
        self.input_stream = self.p.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            input_device_index=self.input_device,
            frames_per_buffer=self.frame_size
        )
        
        self.output_stream = self.p.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.sample_rate,
            output=True,
            output_device_index=self.output_device,
            frames_per_buffer=self.frame_size
        )
        
        self.running = False
        self.tx_thread = None
        self.rx_thread = None
        
        logging.info(f"Audio server initialized - TX on port {self.tx_port}, RX on port {self.rx_port}")

    def start(self):
        self.running = True
        
        self.tx_thread = threading.Thread(target=self._tx_loop, daemon=True)
        self.tx_thread.start()
        
        self.rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self.rx_thread.start()
        
        logging.info("Audio server started")

    def _tx_loop(self):
        self.tx_socket.settimeout(1.0)
        
        while self.running:
            try:
                data, addr = self.tx_socket.recvfrom(4096)
                if not data:
                    continue
                
                with self.client_lock:
                    if self.client_address != addr:
                        self.client_address = addr
                        logging.info(f"Audio client connected: {addr}")
                
                pcm = self.decoder.decode(data, self.frame_size)
                
                if pcm:
                    self.output_stream.write(pcm, num_frames=self.frame_size)
                    
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    logging.error(f"TX audio error: {e}")
                    time.sleep(0.1)

    def _rx_loop(self):
        while self.running:
            try:
                pcm = self.input_stream.read(self.frame_size, exception_on_overflow=False)
                packet = self.encoder.encode(pcm, self.frame_size)
                
                with self.client_lock:
                    if self.client_address:
                        try:
                            client_ip = self.client_address[0]
                            self.rx_socket.sendto(packet, (client_ip, self.rx_port))
                        except Exception as e:
                            logging.warning(f"Failed to send RX audio to client: {e}")
                
            except Exception as e:
                logging.error(f"RX audio capture error: {e}")
                time.sleep(0.1)

    def stop(self):
        self.running = False
        
        if self.tx_thread and self.tx_thread.is_alive():
            self.tx_thread.join(timeout=2.0)
        if self.rx_thread and self.rx_thread.is_alive():
            self.rx_thread.join(timeout=2.0)
        
        try:
            self.input_stream.stop_stream()
            self.input_stream.close()
            self.output_stream.stop_stream()
            self.output_stream.close()
            self.p.terminate()
        except:
            pass
        
        try:
            self.tx_socket.close()
            self.rx_socket.close()
        except:
            pass
        
        logging.info("Audio server stopped")


def list_audio_devices():
    p = pyaudio.PyAudio()
    
    print("Available Audio Devices:")
    print("=" * 50)
    
    for i in range(p.get_device_count()):
        try:
            info = p.get_device_info_by_index(i)
            device_type = []
            if info['maxInputChannels'] > 0:
                device_type.append(f"Input({info['maxInputChannels']} ch)")
            if info['maxOutputChannels'] > 0:
                device_type.append(f"Output({info['maxOutputChannels']} ch)")
            
            print(f"Device {i}: {info['name']}")
            print(f"  Type: {', '.join(device_type)}")
            print(f"  Sample Rate: {info['defaultSampleRate']}")
            print(f"  API: {p.get_host_api_info_by_index(info['hostApi'])['name']}")
            print()
            
        except Exception as e:
            print(f"Device {i}: Error reading device info - {e}")
    
    p.terminate()

if __name__ == "__main__":
    list_audio_devices()
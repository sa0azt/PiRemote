#!/usr/bin/env python3
"""
Full Duplex Audio Implementation for PiRemote
File: audio.py
Handles both TX (microphone) and RX (speaker) audio streams
"""

import configparser
import logging
import socket
import threading
import time
import pyaudio
from opuslib import Encoder, Decoder, APPLICATION_AUDIO

class AudioClient:
    """
    Client-side audio handler (Front Panel)
    - Captures microphone audio and sends to server (TX audio)
    - Receives audio from server and plays to speaker (RX audio)
    """
    def __init__(self, cfg):
        if 'audio' not in cfg:
            raise ValueError("Missing [audio] section in configuration")
            
        sec = cfg['audio']
        self.server_ip = sec.get('server_ip')
        self.tx_port = sec.getint('tx_port', fallback=5001)  # Send mic to server
        self.rx_port = sec.getint('rx_port', fallback=5002)  # Receive from server
        self.sample_rate = sec.getint('sample_rate', fallback=48000)
        self.channels = sec.getint('channels', fallback=1)
        self.frame_size = sec.getint('frame_size', fallback=960)
        
        # Audio device indices
        self.input_device = sec.getint('input_device', fallback=None)
        self.output_device = sec.getint('output_device', fallback=None)
        
        if not self.server_ip:
            raise ValueError("server_ip not configured in [audio] section")

        # Initialize Opus codec
        self.encoder = Encoder(self.sample_rate, self.channels, APPLICATION_AUDIO)
        self.decoder = Decoder(self.sample_rate, self.channels)
        
        # Initialize sockets
        self.tx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # Send mic
        self.rx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # Receive speaker
        self.rx_socket.bind(('0.0.0.0', self.rx_port))

        # Initialize PyAudio
        self.p = pyaudio.PyAudio()
        
        # Input stream (microphone)
        self.input_stream = self.p.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            input_device_index=self.input_device,
            frames_per_buffer=self.frame_size
        )
        
        # Output stream (speaker)
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
        
        # Start TX thread (microphone capture and send)
        self.tx_thread = threading.Thread(target=self._tx_loop, daemon=True)
        self.tx_thread.start()
        
        # Start RX thread (receive and speaker playback)
        self.rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self.rx_thread.start()
        
        logging.info(f"Audio started - TX to {self.server_ip}:{self.tx_port}, RX on port {self.rx_port}")

    def _tx_loop(self):
        """Capture microphone and send to server."""
        while self.running:
            try:
                # Capture microphone audio
                pcm = self.input_stream.read(self.frame_size, exception_on_overflow=False)
                
                # Encode with Opus
                packet = self.encoder.encode(pcm, self.frame_size)
                
                # Send to server
                self.tx_socket.sendto(packet, (self.server_ip, self.tx_port))
                
            except Exception as e:
                logging.error(f"TX audio error: {e}")
                time.sleep(0.1)

    def _rx_loop(self):
        """Receive audio from server and play to speaker."""
        self.rx_socket.settimeout(1.0)
        
        while self.running:
            try:
                # Receive audio packet from server
                data, addr = self.rx_socket.recvfrom(4096)
                if not data:
                    continue
                
                # Decode Opus to PCM
                pcm = self.decoder.decode(data, self.frame_size)
                
                # Play to speaker
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
        
        # Wait for threads
        if self.tx_thread and self.tx_thread.is_alive():
            self.tx_thread.join(timeout=2.0)
        if self.rx_thread and self.rx_thread.is_alive():
            self.rx_thread.join(timeout=2.0)
        
        # Close streams
        try:
            self.input_stream.stop_stream()
            self.input_stream.close()
            self.output_stream.stop_stream()
            self.output_stream.close()
            self.p.terminate()
        except:
            pass
        
        # Close sockets
        try:
            self.tx_socket.close()
            self.rx_socket.close()
        except:
            pass
        
        logging.info("Audio client stopped")


class AudioServer:
    """
    Server-side audio handler (Radio End)
    - Receives microphone audio from client and plays to radio (TX audio)
    - Captures radio audio and sends to client (RX audio)
    """
    def __init__(self, config_path):
        cfg = configparser.ConfigParser()
        cfg.read(config_path)
        
        if 'audio' not in cfg:
            raise ValueError("Missing [audio] section in configuration")
            
        sec = cfg["audio"]
        self.listen_ip = sec.get("listen_ip", fallback="0.0.0.0")
        self.tx_port = sec.getint("tx_port", fallback=5001)  # Receive mic from client
        self.rx_port = sec.getint("rx_port", fallback=5002)  # Send radio audio to client
        self.sample_rate = sec.getint("sample_rate", fallback=48000)
        self.channels = sec.getint("channels", fallback=1)
        self.frame_size = sec.getint("frame_size", fallback=960)
        
        # Audio device indices
        self.input_device = sec.getint('input_device', fallback=None)   # Radio RX
        self.output_device = sec.getint('output_device', fallback=None) # Radio TX
        
        # Initialize Opus codec
        self.encoder = Encoder(self.sample_rate, self.channels, APPLICATION_AUDIO)
        self.decoder = Decoder(self.sample_rate, self.channels)
        
        # Initialize sockets
        self.tx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # Receive mic
        self.tx_socket.bind((self.listen_ip, self.tx_port))
        
        self.rx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # Send radio audio
        
        # Store client address for RX audio
        self.client_address = None
        self.client_lock = threading.Lock()

        # Initialize PyAudio
        self.p = pyaudio.PyAudio()
        
        # Input stream (radio RX audio)
        self.input_stream = self.p.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            input_device_index=self.input_device,
            frames_per_buffer=self.frame_size
        )
        
        # Output stream (radio TX audio)
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
        
        logging.info(f"Audio server initialized - TX on {self.listen_ip}:{self.tx_port}, RX on port {self.rx_port}")

    def start(self):
        self.running = True
        
        # Start TX thread (receive mic from client, play to radio)
        self.tx_thread = threading.Thread(target=self._tx_loop, daemon=True)
        self.tx_thread.start()
        
        # Start RX thread (capture radio, send to client)
        self.rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self.rx_thread.start()
        
        logging.info("Audio server started")

    def _tx_loop(self):
        """Receive microphone audio from client and play to radio."""
        self.tx_socket.settimeout(1.0)
        
        while self.running:
            try:
                # Receive audio packet from client
                data, addr = self.tx_socket.recvfrom(4096)
                if not data:
                    continue
                
                # Store client address for RX direction
                with self.client_lock:
                    if self.client_address != addr:
                        self.client_address = addr
                        logging.info(f"Audio client connected: {addr}")
                
                # Decode Opus to PCM
                pcm = self.decoder.decode(data, self.frame_size)
                
                # Play to radio (TX audio)
                if pcm:
                    self.output_stream.write(pcm, num_frames=self.frame_size)
                    
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    logging.error(f"TX audio error: {e}")
                    time.sleep(0.1)

    def _rx_loop(self):
        """Capture radio audio and send to client."""
        while self.running:
            try:
                # Capture radio RX audio
                pcm = self.input_stream.read(self.frame_size, exception_on_overflow=False)
                
                # Encode with Opus
                packet = self.encoder.encode(pcm, self.frame_size)
                
                # Send to client (if connected)
                with self.client_lock:
                    if self.client_address:
                        try:
                            # Send to client's RX port (not the port they sent from)
                            client_ip = self.client_address[0]
                            self.rx_socket.sendto(packet, (client_ip, self.rx_port))
                        except Exception as e:
                            logging.warning(f"Failed to send RX audio to client: {e}")
                
            except Exception as e:
                logging.error(f"RX audio capture error: {e}")
                time.sleep(0.1)

    def stop(self):
        self.running = False
        
        # Wait for threads
        if self.tx_thread and self.tx_thread.is_alive():
            self.tx_thread.join(timeout=2.0)
        if self.rx_thread and self.rx_thread.is_alive():
            self.rx_thread.join(timeout=2.0)
        
        # Close streams
        try:
            self.input_stream.stop_stream()
            self.input_stream.close()
            self.output_stream.stop_stream()
            self.output_stream.close()
            self.p.terminate()
        except:
            pass
        
        # Close sockets
        try:
            self.tx_socket.close()
            self.rx_socket.close()
        except:
            pass
        
        logging.info("Audio server stopped")


# Utility function to list audio devices
def list_audio_devices():
    """List all available audio devices for configuration."""
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
    # Utility to list audio devices
    list_audio_devices()
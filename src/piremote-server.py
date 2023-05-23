#!/usr/bin/env python3
import sys
import socket
import serial
import serial.threaded
import time
import configparser

# Get config from .env file
config = configparser.ConfigParser()
config.read('piremote.conf')

class SerialToNet(serial.threaded.Protocol):
    """serial->socket"""

    def __init__(self):
        self.socket = None

    def __call__(self):
        return self

    def data_received(self, data):
        if self.socket is not None:
            self.socket.sendall(data)


if __name__ == '__main__':

    # Settings
    serial_port = config.get('SERVER', 'SERIAL_PORT')
    serial_baud = config.get('SERVER', 'SERIAL_BAUD')
    port = config.get('SERVER', 'PORT')

    # Connect to serial port
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
        sys.stderr.write('Could not open serial port {}: {}\n'.format(ser.name, e))
        sys.exit(1)

    ser_to_net = SerialToNet()
    serial_worker = serial.threaded.ReaderThread(ser, ser_to_net)
    serial_worker.start()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('', int(port)))
    srv.listen(1)

    try:
        intentional_exit = False
        while True:
            sys.stderr.write('Waiting for connection on {}...\n'.format(port))
            client_socket, addr = srv.accept()
            sys.stderr.write('Connected by {}\n'.format(addr))
            try:
                client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 1)
                client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 1)
                client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
                client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except AttributeError:
                pass
            client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            try:
                ser_to_net.socket = client_socket
                while True:
                    try:
                        data = client_socket.recv(1024)
                        if not data:
                            break
                        sys.stderr.write('DEBUG: '.format(data))
                        ser.write(data)
                    except socket.error as msg:
                        sys.stderr.write('ERROR: {}\n'.format(msg))
                        break
            except KeyboardInterrupt:
                intentional_exit = True
                raise
            except socket.error as msg:
                sys.stderr.write('ERROR: {}\n'.format(msg))
            finally:
                ser_to_net.socket = None
                sys.stderr.write('Client disconnected\n')
                client_socket.close()
    except KeyboardInterrupt:
        pass

    sys.stderr.write('\n--- Server stopped ---\n')
    serial_worker.stop()

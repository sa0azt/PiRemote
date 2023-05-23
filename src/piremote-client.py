#!/usr/bin/env python3
import RPi.GPIO as GPIO
import sys
import os
import socket
import select
import serial
import serial.threaded
import time
import configparser
import fnmatch
import threading

io_pwr = 23
io_input1 = 7
io_input2 = 0
io_input3 = 0
io_output1 = 0
io_output2 = 0
io_output3 = 0
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(io_pwr, GPIO.OUT)
GPIO.setup(io_input1, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

config = configparser.ConfigParser(converters={'list': lambda x: [i.strip() for i in x.split(',')]})
config.read('piremote.conf')
debug = config.get('MAIN', 'DEBUG')
serial_port = config.get('CLIENT', 'SERIAL_PORT')
serial_baud = config.get('CLIENT', 'SERIAL_BAUD')
trx_list = config.getlist('CLIENT', 'TRX_LIST')
trx_index = 0
host, port = trx_list[trx_index].split(':')
ser = serial.serial_for_url(serial_port, do_not_open=True)
ser.baudrate = serial_baud
ser.bytesize = 8
ser.parity = 'N'
ser.stopbits = 1
ser.rtscts = True
ser.xonxoff = False
running = False
changeRadio = False
CtrlClient = 0
Remotetrx = 0

try:
    ser.open()
except serial.SerialException as e:
    sys.stderr.write('Could not open serial port {}: {}\n'.format(ser.name, e))
    sys.exit(1)

class SerialToNet(serial.threaded.Protocol):
    """serial->socket"""

    def __init__(self):
        self.socket = None

    def __call__(self):
        return self

    def data_received(self, data):
        if debug == True:
            sys.stderr.write(str(data))
        if self.socket is not None:
            self.socket.sendall(data)

class CtrlTCPClient(threading.Thread):

    global host
    global port

    def __init__(self):
        threading.Thread.__init__(self)
        self.event = threading.Event()
        self.ser_to_net = SerialToNet()
        self.serial_worker = serial.threaded.ReaderThread(ser, self.ser_to_net)
        self.serial_worker.start()

    def run(self):
        while not self.event.is_set():
            sys.stderr.write("Opening connection to {}:{}...\n".format(host, port))
            client_socket = socket.socket()
            try:
                client_socket.connect((host, int(port)))
            except socket.error as msg:
                sys.stderr.write('WARNING: {}\n'.format(msg))
                time.sleep(1)  # intentional delay on reconnection as client
                continue
            sys.stderr.write('Connected\n')
            client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            try:
                self.ser_to_net.socket = client_socket
                while not self.event.is_set():
                    try:
                        dataReceived = select.select([client_socket], [], [], 0.5)
                        if dataReceived[0]:
                            data = client_socket.recv(1024)
                            #print(data)
                            if fnmatch.fnmatch(str(data), '* #[0-9] *'):
                                SwitchRadio(str(data))
                            sys.stderr.write('DEBUG: '.format(data))
                            ser.write(data)
                    except socket.error as msg:
                        sys.stderr.write('ERROR: {}\n'.format(msg))
                        break
            except socket.error as msg:
                sys.stderr.write('ERROR: {}\n'.format(msg))
                break
            finally:
                self.ser_to_net.socket = None
                sys.stderr.write('Disconnected\n')
                client_socket.close()

        self.serial_worker.stop()
        self.ser_to_net.socket = None
        client_socket.close()

    def stop(self):
        self.event.set()

class RemotetrxClient(threading.Thread):

    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        sys.stderr.write("Starting remotetrx with remotetrx_" + str(trx_index) + ".conf\n")
        os.system("remotetrx --runasuser svxlink --daemon --config=/etc/piremote/remotetrx_" + str(trx_index) + ".conf\n")

    def stop(self):
        sys.stderr.write("Stopping remotetrx!\n")
        os.system("killall -s SIGKILL remotetrx\n")

def SwitchRadio(data):

    global changeRadio
    global trx_index
    global host
    global port

    trx_index = int((data.split("#", 1)[1]).split(" ", 1)[0])
    host, port = trx_list[trx_index].split(':')
    sys.stderr.write("Switching to radio #" + str(trx_index) + "\n")
    print(trx_index)
    print(host)
    print(port)
    changeRadio = True
    Run(True)

def Pwr(state):

    if state:
        sys.stderr.write("Power on\n")
        GPIO.output(io_pwr, GPIO.HIGH)
    else:
        sys.stderr.write("Power off\n")
        GPIO.output(io_pwr, GPIO.LOW)

def Run(var):

    global running
    global CtrlClient
    global Remotetrx
    global changeRadio

    if not running:
        CtrlClient = CtrlTCPClient()
        CtrlClient.start()
        Remotetrx = RemotetrxClient()
        Remotetrx.start()
        Pwr(True)
        running = True
        changeRadio = False
    elif running == True:
        CtrlClient.stop()
        Remotetrx.stop()
        running = False
        Pwr(False)
        if changeRadio == True:
            time.sleep(1)
            Run(True)

GPIO.add_event_detect(io_input1, GPIO.BOTH, callback=Run, bouncetime=500)

while True: time.sleep(0.2)
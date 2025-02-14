



# -*- coding: utf-8 -*-
import struct
import os
import atexit
import time
from time import time as now
from threading import Thread
from glob import glob
try:
    from queue import Queue
except ImportError:
    from Queue import Queue

event_bin_format = 'llHHI'

# Taken from include/linux/input.h
# https://www.kernel.org/doc/Documentation/input/event-codes.txt
EV_SYN = 0x00
EV_KEY = 0x01
EV_REL = 0x02
EV_ABS = 0x03
EV_MSC = 0x04

def make_uinput():
    if not os.path.exists('/dev/uinput'):
        raise IOError('No uinput module found.')

    import fcntl, struct

    # Requires uinput driver, but it's usually available.
    uinput = open("/dev/uinput", 'wb')
    UI_SET_EVBIT = 0x40045564
    fcntl.ioctl(uinput, UI_SET_EVBIT, EV_KEY)

    UI_SET_KEYBIT = 0x40045565
    for i in range(256):
        fcntl.ioctl(uinput, UI_SET_KEYBIT, i)

    BUS_USB = 0x03
    uinput_user_dev = "80sHHHHi64i64i64i64i"
    axis = [0] * 64 * 4
    uinput.write(struct.pack(uinput_user_dev, b"Virtual Keyboard", BUS_USB, 1, 1, 1, 0, *axis))
    uinput.flush() # Without this you may get Errno 22: Invalid argument.

    UI_DEV_CREATE = 0x5501
    fcntl.ioctl(uinput, UI_DEV_CREATE)
    UI_DEV_DESTROY = 0x5502
    #fcntl.ioctl(uinput, UI_DEV_DESTROY)

    return uinput

class EventDevice(object):
    def __init__(self, path):
        self.path = path
        self._input_file = None
        self._output_file = None

    @property
    def input_file(self):
        if self._input_file is None:
            try:
                self._input_file = open(self.path, 'rb')
            except IOError as e:
                if e.strerror == 'Permission denied':
                    print('Permission denied ({}). You must be sudo to access global events.'.format(self.path))
                    exit()

            def try_close():
                try:
                    self._input_file.close
                except:
                    pass
            atexit.register(try_close)
        return self._input_file

    @property
    def output_file(self):
        if self._output_file is None:
            self._output_file = open(self.path, 'wb')
            atexit.register(self._output_file.close)
        return self._output_file

    def read_event(self):
        data = self.input_file.read(struct.calcsize(event_bin_format))
        seconds, microseconds, type, code, value = struct.unpack(event_bin_format, data)
        return seconds + microseconds / 1e6, type, code, value, self.path

    def write_event(self, type, code, value):
        integer, fraction = divmod(now(), 1)
        seconds = int(integer)
        microseconds = int(fraction * 1e6)
        data_event = struct.pack(event_bin_format, seconds, microseconds, type, code, value)

        # Send a sync event to ensure other programs update.
        sync_event = struct.pack(event_bin_format, seconds, microseconds, EV_SYN, 0, 0)

        self.output_file.write(data_event + sync_event)
        self.output_file.flush()

class AggregatedEventDevice(object):
    def __init__(self, output=None):
        self.event_queue = Queue()
        self.output = output

        def start_reading(device):
            while True:
                self.event_queue.put(device.read_event())

        def mng():
            latest_paths = []
            while True:
                devices = list(list_devices_from_proc('kbd'))
                new_devices = [d for d in devices if d.path not in latest_paths]
                latest_paths = [d.path for d in devices]
                for new_device in new_devices:
                    thread = Thread(target=start_reading, args=[new_device])
                    thread.setDaemon(True)
                    thread.start()
                time.sleep(0.5)

        thread = Thread(target=mng, args=[])
        thread.setDaemon(True)
        thread.start()

    def read_event(self):
        return self.event_queue.get(block=True)

    def write_event(self, type, code, value):
        self.output.write_event(type, code, value)

import re
from collections import namedtuple
DeviceDescription = namedtuple('DeviceDescription', 'event_file is_mouse is_keyboard')
device_pattern = r"""N: Name="([^"]+?)".+?H: Handlers=([^\n]+)"""
def list_devices_from_proc(type_name):
    try:
        with open('/proc/bus/input/devices') as f:
            description = f.read()
    except FileNotFoundError:
        return

    devices = {}
    for name, handlers in re.findall(device_pattern, description, re.DOTALL):
        path = '/dev/input/event' + re.search(r'event(\d+)', handlers).group(1)
        if type_name in handlers:
            yield EventDevice(path)

def list_devices_from_by_id(name_suffix, by_id=True):
    for path in glob('/dev/input/{}/*-event-{}'.format('by-id' if by_id else 'by-path', name_suffix)):
        yield EventDevice(path)

def aggregate_devices(type_name):
    # Some systems have multiple keyboards with different range of allowed keys
    # on each one, like a notebook with a "keyboard" device exclusive for the
    # power button. Instead of figuring out which keyboard allows which key to
    # send events, we create a fake device and send all events through there.
    try:
        uinput = make_uinput()
        fake_device = EventDevice('uinput Fake Device')
        fake_device._input_file = uinput
        fake_device._output_file = uinput
    except IOError as e:
        import warnings
        warnings.warn('Failed to create a device file using `uinput` module. Sending of events may be limited or unavailable depending on plugged-in devices.', stacklevel=2)
        fake_device = None

    # We don't aggregate devices from different sources to avoid
    # duplicates.

    return AggregatedEventDevice(output=fake_device)


def ensure_root():
    if os.geteuid() != 0:
        raise ImportError('You must be root to use this library on linux.')

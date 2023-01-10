#!/usr/bin/env python3

# Copyright 2022 Scott A. Anderson
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import argparse
import functools
import sys
import time
import traceback

from pexpect import fdpexpect
from pexpect.exceptions import TIMEOUT
import serial

class DatamanS4:
    ESCAPE = '\x1B'
    S4_MEM_SIZE = 512 * 1024

    def __init__(self, port, baud, size = None, debug=False):
        self.mem_start = 0
        if size is None:
            size = DatamanS4.S4_MEM_SIZE
        self.mem_size = size

        self.ser = serial.Serial(port, baud, rtscts = True)
        self.exp = fdpexpect.fdspawn(self.ser.fileno(), timeout=0.5)

        self.debug = debug

        # To ensure we are in sync with the S4, send an escape character,
        # delay for a short while, flush whatever input we received on the
        # serial port and finally send a carriage return expecting to receive
        # a prompt.
        self.exp.send(self.ESCAPE)
        time.sleep(0.2)
        self.ser.reset_input_buffer()
        self.exp.send('\r')
        self._expect('\r\n>')

    @property
    def mem_end(self):
        return self.mem_start + self.mem_size - 1

    @mem_end.setter
    def mem_end(self, end):
        self.mem_size = end - self.mem_start + 1

    def _expect(self, expected, timeout=-1):
        self.exp.expect(expected, timeout=timeout)
        assert self.exp.before == b''
        if self.debug:
            print(f'expected {expected!r:64} got {self.exp.after!r}',
                  file=sys.stderr)

    def _send(self, to_send):
        self.exp.send(to_send)
        if self.debug:
            print(f'sent {to_send!r}', file=sys.stderr)

    def _set_named_byte(self, name, value=None, last_value=False):
        assert(value is None or 0 <= value <= 255)
        expected = f'^\r\n{name:15}([0-9A-F]{{2}})\b\b'
        self._expect(expected)
        current_value = int(self.exp.match.group(1), 16)
        if value is not None and value != current_value:
            self._send(f'{value:02X}')
            self._expect(f'^{value:02X}\b')
        if last_value:
            self._send(self.ESCAPE)
        else:
            self._send('\r')

    def advanced_setup(self, mute=None):
        # FUNC SETUP = Advanced Page 68
        if mute is None:
            high_tone = low_tone = busy_tone = None
        elif mute:
            high_tone = low_tone = busy_tone = 0
        else:
            high_tone = 0x98
            low_tone = 0xAC
            busy_tone = 0x50

        self._send('as')
        self._expect('^AS\r> ADVANCED SETUP')
        self._set_named_byte('Shutdown Time')
        self._set_named_byte('High Tone', high_tone)
        self._set_named_byte('Low Tone', low_tone)
        self._set_named_byte('Busy Tone', busy_tone)
        self._set_named_byte('Max Batt Temp')
        self._set_named_byte('Min Batt Temp')
        self._set_named_byte('Charge Time')
        self._set_named_byte('Discharge Time')
        self._set_named_byte('Deep Discharge')
        self._set_named_byte('Norm Discharge', last_value=True)
        self._expect('^\r\n>')

    def _get_checksum(self, size):
        # I measured that it takes the S4 a little under 21 seconds to
        # checksum all 512K of its memory.
        timeout = self.exp.timeout + size * 21 / self.S4_MEM_SIZE
        self._expect('^\r\nSUM = ([0-9A-F]{8})\r\n>', timeout=timeout)
        return int(self.exp.match.group(1), 16)

    def checksum_device(self):
        # SUM KEY green (CR) page 49
        self._send('cr')
        self._expect('^CR\r>CHKSUM [^\r]+')
        # Use mem_size as a proxy for actually knowing the target device size
        return self._get_checksum(self.mem_size)

    def _set_start_end(self):
        self._expect('^\r\n[0-9A-F]{5},[0-9A-F]{5},\b \b{12}')
        # Initially, I transmitted all the digits at once, but the S4
        # would occasionally drop characters when responding.
        to_send = f'{self.mem_start:05X}'
        for each_ch in to_send:
            self._send(each_ch)
            self._expect(f'^{each_ch}')
        self._expect(',')
        to_send = f'{self.mem_end:05X}'
        for each_ch in to_send:
            self._send(each_ch)
            self._expect(f'^{each_ch}')
        self._expect('^\b')
        self._send('\r')
        self._expect('^\r')

    def checksum_mem(self):
        # SUM KEY grey (CH) page 58
        self._send('ch')
        self._expect('^CH\r>CHECKSUM RAM')
        self._set_start_end()
        return self._get_checksum(self.mem_size)

    def data_to_s4(self, data):
        # RCVE (RE) pg 63
        # TODO: Add option to fill with specified character if too short.
        assert len(data) == self.mem_size
        self._send('re')
        self._expect('^RE\r>RECEIVE BINARY  ')
        self._set_start_end()
        self._send(data)
        self.ser.flush()
        time.sleep(1.1)
        self._expect('^\r\n>')

    def data_from_s4(self):
        # SEND (SE) pg 62
        self._send('se')
        self._expect('^SE\r>SEND BINARY  ')
        self._set_start_end()
        self._expect(f'^\r\n(.{{{self.mem_size}}})')
        data = self.exp.match.group(1)
        self._expect('^\r\n>')
        return data

    def emulate(self):
        # EMUL (EM) pg 41
        self._send('em')
        self._expect('^EM\r>EMULATE [^\r]+')

def main():
    prog_description = 's4: Send a binary file to a Dataman S4'
    argp = argparse.ArgumentParser(description=prog_description)
    argp.add_argument('-p', '--port', default='/dev/ttyUSB0',
                      help='Serial port that connects to Dataman S4')
    argp.add_argument('-b', '--baud', type=int, default=115200,
                      choices=[300, 600, 1200, 2400, 4800,
                               9600, 14400, 28800, 115200],
                      help='The baud rate of the Dataman S4')
    argp.add_argument('-a', '--start-address',
                      type=functools.partial(int, base=0), default=0,
                      help='The data starting address in Dataman S4 RAM')
    argp.add_argument('-l', '--length',
                      type=functools.partial(int, base=0), default=0x0800,
                      help='The data length')
    argp.add_argument('-m', '--mute', action='store_true',
                      help='Configure the Dataman S4 to not beep as much')
    argp.add_argument('-e', '--emulate', action='store_true',
                      help='Have the Dataman S4 emulate a memory sent')
    argp.add_argument('-g', '--debug', action='store_true',
                      help='Output Dataman S4 traffic to stdout')
    argp.add_argument('-v', '--verbose', action='store_true',
                      help='Output checksum to stdout')
    argp.add_argument('binary_file', type=argparse.FileType('rb'),
                      help='The binary file to send to the Dataman S4')
    args = argp.parse_args()

    s4 = DatamanS4(args.port, args.baud, # pylint: disable=invalid-name
                   debug=args.debug)
    s4.mem_start = args.start_address
    s4.mem_size = args.length
    try:
        if args.mute:
            s4.advanced_setup(mute=True)
        data_to = args.binary_file.read()
        s4.data_to_s4(data_to)
        chksum = sum(data_to)
        if args.verbose:
            print(f'0x{chksum:08X}')
        assert chksum == s4.checksum_mem()
        if args.emulate:
            s4.emulate()

        # Examples of other functions:
        # data_from = s4.data_from_s4()
        # assert chksum == sum(data_from)
        # print(f'0x{s4.checksum_device():08X}')

    except TIMEOUT as exc:
        print('==== EXCEPTION ====')
        traceback.print_exception(None, exc, exc.__traceback__)
        print(f'{s4.exp.before =}')
        print(f'{s4.exp.after =}')
        return 1

    return 0

if __name__ == '__main__':
    sys.exit(main())

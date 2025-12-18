#!/usr/bin/env python3

"""Python interface to Dataman S4."""

# BSD Zero Clause License
#
# Copyright (c) 2025 Scott A. Anderson
#
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
# REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
# INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
# LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
# OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
# PERFORMANCE OF THIS SOFTWARE.

import argparse
from enum import StrEnum
import os
import re
import sys
import textwrap
from typing import cast, Final
import serial

VERSION: Final = '0.1'
DEFAULT_DEV: Final = '/dev/ttyUSB0'
DEFAULT_TIMEOUT: Final = 0.3


def progname() -> str:
    """Return the name of this program."""
    return os.path.basename(__file__)


class CommunicationError(Exception):
    """Exception indicating an error in communicating with the Dataman S4."""


class DatamanS4():
    """A class that abstracts communication with an Dataman S4."""
    ESCAPE: Final = b'\x1B'

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args

        if self.logging_debug():
            print(f'Opening serial device "{args.tty_device}".',
                  file=sys.stderr)
        self.serial = serial.Serial(port=args.tty_device,
                                    baudrate=args.baud_rate)

        self.serial.timeout = args.timeout
        if self.logging_debug():
            print(f'Timeout set to {self.serial.timeout}.', file=sys.stderr)
            print('Synchronizing with Dataman S4.', file=sys.stderr)
        self.write(self.ESCAPE)  # Interrupt any outstanding command
        self.write(b'\r')  # Try to get a prompt
        synchronized = False
        while line := self.readline():  # Keep reading until timeout
            if line == b'>':
                synchronized = True
        if not synchronized:
            raise CommunicationError('Could not synchronize with Dataman S4.  '
                                     'Try pressing the orange ESC key on it.')

        self.device_type, self.start, self.end = self.get_device_information()
        self.length = self.end - self.start + 1

        if self.logging_info():
            print(f'Dataman S4 device: {self.length} byte {self.device_type} '
                  f'with address range of '
                  f'0x{self.start:05X} to 0x{self.end:05X}.', file=sys.stderr)

    # Helper methods
    def logging_info(self) -> bool:
        """Should info be logged to stderr?"""
        return cast(int, self.args.verbose) >= 1  # cast() is for mypy

    def logging_debug(self) -> bool:
        """Should debug be logged to stderr?"""
        return cast(int, self.args.verbose) >= 2  # cast() is for mypy

    def logging_serial(self) -> bool:
        """Should serial traffic be logged to stderr?"""
        return cast(int, self.args.verbose) >= 3  # cast() is for mypy

    def log_response(self, response: bytes) -> bytes:
        """Log serial <response> if appropriate and then return <response>."""
        if self.logging_serial():
            print(f'<<<< {response!r}', file=sys.stderr)
        return response

    def read(self, size: int = 1) -> bytes:
        """Read <size> bytes from the Dataman S4."""
        response = cast(bytes, self.serial.read(size))
        return self.log_response(response)

    def read_until(self,
                   expected: bytes = b'\n', size: int | None = None) -> bytes:
        """Read until <size> bytes or <expected> is received or timeout."""
        response = cast(bytes, self.serial.read_until(expected, size))
        return self.log_response(response)

    def readline(self) -> bytes:
        """Read one line from the Dataman S4."""
        line = cast(bytes, self.serial.readline())
        return self.log_response(line)

    def write(self, command: bytes) -> None:
        """Send <command> to the Dataman S4."""
        if self.logging_serial():
            print(f'>>>> {command!r}', file=sys.stderr)
        self.serial.write(command)
        self.serial.flush()

    # Methods for using the Dataman S4 key functions
    def get_device_information(self) -> tuple[str, int, int]:
        """Use green key TEST on the Dataman S4 to get device information.

        Returns (<device type>, <start>, <end>).
        """
        if self.logging_debug():
            print('Using TEST function to get device information.',
                  file=sys.stderr)
        # Invoke TEST
        self.write(b'PR')
        # Get the first line of the response which contains the device type
        response = self.readline()
        device_match = re.fullmatch(b'PR\r>PRETEST ([^\r]*)\r\n', response)
        if not device_match:
            raise CommunicationError(f'Unexpected PRETEST response '
                                     f'{response!r}')
        device = device_match.group(1)

        # Get the remainder of the response which contains start, end and dest
        parms_len = len(b'\r 00000-7FFFF=00000'
                        b'\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b\b')
        parms_response = self.read(parms_len)

        # Escape out of the pretest command and read resultant output
        self.write(self.ESCAPE)
        response = self.read_until(b'>', 32)
        if response != b'\r\nEsc\r\n>':
            raise CommunicationError(f'Unexpected PRETEST response '
                                     f'{response!r}')

        parms_match = re.fullmatch(b'\r ([0-9A-F]{5})-([0-9A-F]{5})'
                                   b'([=#])[0-9A-F]{5}\b{17}', parms_response)
        if parms_match and parms_match.group(3) == b'#':
            raise CommunicationError('Use TEST key to reset range to cover '
                                     'entire device range.')
        try:
            device_type = device.decode('ascii')
            start = int(parms_match.group(1), 16)  #type: ignore
            end = int(parms_match.group(2), 16)  #type: ignore
        except (AttributeError, ValueError) as exc:
            raise CommunicationError(f'Unexpected PRETEST response '
                                     f'{parms_response!r}') from exc
        if self.logging_debug():
            print(f'Device type:"{device_type}" address range '
                  f'0x{start:05X}-0x{end:05X}', file=sys.stderr)
        return (device_type, start, end)

    class FileFormat(StrEnum):
        """The file formats that the Dataman S4 knows."""
        ASCII    = '\rASCII   '  # The values are what the S4 echoes back
        INTEL    = '\rINTEL   '
        MOTOROLA = '\rMOTOROLA'
        TEKHEX   = '\rTEK HEX '
        BINARY   = '\rBINARY  '

    def set_file_format(self, file_format: FileFormat) -> None:
        """Set the file format on the Dataman S4 (subset of SETUP key)."""
        if self.logging_debug():
            print(f'Setting Dataman S4 filetype to {file_format.name}.',
                  file=sys.stderr)
        self.write(b'FF')
        response = self.readline()
        if response != b'FF\r>FILE FORMAT\r\n':
            raise CommunicationError(f'Unexpected FILE FORMAT response '
                                     f'{response!r}')

        all_file_formats = {bytes(file_format.value, encoding='ascii')
                            for file_format in self.FileFormat}

        for _try_count in range(len(all_file_formats)):
            current_file_format = self.read(9)
            if current_file_format.decode('ascii') == file_format.value:
                # Lock the new file format in.
                self.write(b'\r')
                response = self.read_until(b'>', 32)
                if response != b'\r\n>':
                    raise CommunicationError(f'Unexpected FILE FORMAT response '
                                             f'{response!r}')
                break
            if current_file_format not in all_file_formats:
                self.write(self.ESCAPE)  # Cancel changing the file format
                raise CommunicationError(f'Unexpected FILE FORMAT response '
                                         f'{response!r}')
            self.write(b' ')  # Try the next file format
        else:
            raise CommunicationError(f'File format {file_format.name} '
                                     f'not found.')

    def _set_start_end(self, start: int, end: int) -> None:
        """Sets <start> and <end> addresses for a Dataman S4 function."""
        # Get the remainder of the response which contains the start and end
        parms_len = len(b'00000,7FFFF,\x08 '
                        b'\x08\x08\x08\x08\x08\x08\x08\x08\x08\x08\x08\x08')
        response = self.read(parms_len)
        parms_match = re.fullmatch(b'([0-9A-F]{5}),([0-9A-F]{5}),\b \b{12}',
                                   response)
        if not parms_match:
            raise CommunicationError(f'Unexpected CHECKSUM RAM response '
                                     f'{response!r}')

        # Set the range of to checksum to match the range of the device
        self.write(f'{start:05X}{end:05X}'.encode('ascii'))

        # Read the echo
        expected = f'{start:05X},{end:05X}\b'.encode('ascii')
        response = self.read(len(expected))
        if response != expected:
            raise CommunicationError(f'Unexpected CHECKSUM RAM response '
                                     f'{response!r}')

    def set_ram(self, data: bytes, start: int | None = None) -> None:
        """Write <data> to Dataman S4 RAM starting at address <start>."""
        if start is None:
            start = self.start
        end = start + len(data) - 1

        self.set_file_format(DatamanS4.FileFormat.BINARY)

        if self.logging_debug():
            print(f'Transmitting 0x{len(data):05X} bytes to Dataman S4 RAM '
                  f'starting at address 0x{start:05X}.', file=sys.stderr)

        # Invoke RECEIVE
        self.write(b'RE')
        response = self.readline()
        if response != b'RE\r>RECEIVE BINARY  \r\n':
            raise CommunicationError(f'Unexpected RECEIVE BINARY response '
                                     f'{response!r}')
        self._set_start_end(start, end)  # Set the start and end addresses
        self.write(b'\r')  # Start the receive
        response = self.read(1)
        if response != b'\r':
            raise CommunicationError(f'Unexpected RECEIVE BINARY response '
                                     f'{response!r}')
        self.write(data)  # Send the data to the Dataman S4
        old_timeout = self.serial.timeout
        self.serial.timeout = 80
        response = self.read_until(b'>', 32)
        self.serial.timeout = old_timeout
        if response != b'\r\n>':
            raise CommunicationError(f'Unexpected RECEIVE BINARY response '
                                     f'{response!r}')
        if self.logging_debug():
            print('Transmission complete.', file=sys.stderr)

    def get_ram_checksum(self, start: int | None = None,
                         end: int | None = None) -> int:
        """Use grey key SUM on the Dataman S4 to get RAM checksum."""
        if start is None:
            start = self.start
        if end is None:
            end = self.end
        if self.logging_debug():
            print(f'Getting checksum of Dataman S4 RAM address range '
                  f'0x{start:05X}-0x{end:05X}.', file=sys.stderr)
        # Invoke SUM
        self.write(b'CH')
        response = self.readline()
        if response != b'CH\r>CHECKSUM RAM\r\n':
            raise CommunicationError(f'Unexpected CHECKSUM RAM response '
                                     f'{response!r}')

        self._set_start_end(start, end)  # Set the start and end addresses
        self.write(b'\r')  # Start the checksumming

        # Wait for and read the resultant checksum
        old_timeout = self.serial.timeout
        self.serial.timeout = 30
        response = self.readline()
        self.serial.timeout = old_timeout
        if response != b'\r\r\n':
            raise CommunicationError(f'Unexpected CHECKSUM RAM response '
                                     f'{response!r}')
        response = self.read_until(b'>', 32)  # e.g. b'SUM = 07F80000\r\n>'

        # Now parse the response
        checksum_match = re.fullmatch(b'SUM = ([0-9A-F]{8})\r\n>', response)
        try:
            checksum = int(checksum_match.group(1), 16)  #type: ignore
        except (AttributeError, ValueError) as exc:
            raise CommunicationError(f'Unexpected CHECKSUM RAM response '
                                     f'{response!r}') from exc
        if self.logging_debug():
            print(f'Checksum: ' f'0x{checksum:08X}', file=sys.stderr)
        return checksum


def parse_args() -> argparse.Namespace:
    """Parse the command line arguments."""

    epilog: Final = textwrap.dedent(f"""
    The default TTY device is {DEFAULT_DEV}.
    The default timeout is {DEFAULT_TIMEOUT} seconds.
    """)

    formatter = argparse.RawDescriptionHelpFormatter
    parser = argparse.ArgumentParser(description=__doc__, prog=progname(),
                                     formatter_class=formatter,
                                     add_help=False, epilog=epilog)
    parser.add_argument('-d', '--tty-device', default=DEFAULT_DEV,
                        help='TTY device connected to the Dataman S4')
    parser.add_argument('-b', '--baud-rate', default=115200, type=int,
                        help='TTY baud rate')
    parser.add_argument('-t', '--timeout', default=DEFAULT_TIMEOUT, type=float,
                        help='Timeout in seconds for reading each parameter.')
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help='Increase verbosity')
    parser.add_argument('-V', '--version',
                        action='version', version='%(prog)s v' + VERSION,
                        help='Output %(prog)s version information and exit')
    parser.add_argument('-h', '--help', action='help',
                        help='Display this help and exit')
    parser.add_argument('binfile', help='Binary to write to Dataman S4 RAM')

    return parser.parse_args()


def main() -> int:
    """The main event."""
    args = parse_args()
    dm_s4 = DatamanS4(args)

    with open(args.binfile, 'rb') as binfile:
        data = binfile.read()

    if len(data) != dm_s4.length:
        print(f'The size of {args.binfile} ({len(data)} bytes) != '
              f'{dm_s4.device_type} size ({dm_s4.length} bytes).',
              file=sys.stderr)
        return 1

    data_checksum = sum(data) & 0xFFFFFFFF
    dm_s4.set_ram(data)
    ram_checksum = dm_s4.get_ram_checksum()

    if data_checksum != ram_checksum:
        print(f'{args.binfile}: ERROR: '
              f'RAM checksum (0x{ram_checksum:08X}) does not match'
              f'file checksum (0x{data_checksum:08X})!', file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('Keyboard interrupt', file=sys.stderr)
    except BrokenPipeError:
        print('Broken pipe', file=sys.stderr)
    sys.exit(255)

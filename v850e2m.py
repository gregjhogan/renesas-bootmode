import sys
import os
import time
import struct
import serial
from hexdump import hexdump
from tqdm import tqdm

SERIALPORT = os.getenv('SERIAL_PORT', '/dev/ttyUSB0')
BAUDRATE = 9600
DEBUG = os.getenv('DEBUG', False)

def pulse(ser):
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    print('[PULSE]')
    time.sleep(0.2)
    ser.write(b'\x00')
    time.sleep(0.02)
    ser.write(b'\x00')
    time.sleep(0.02)

    # single wire serial will have echo
    ser.reset_input_buffer()

def get_checksum(req):
    chksum = -sum(req)
    return bytes([chksum & 0xFF])

def send_request(ser, id, data=None):
    if DEBUG: print('TX --->')
    ack = id == b'\x06'
    # acknowledgement is a data frame
    h = b'\x01' if ack == False else b'\x11'
    l = len(data) if data is not None else 0
    req = struct.pack('!H', l+1) + id
    if data: req += data
    req += get_checksum(req)
    f = b'\x03'
    if DEBUG: hexdump(h + req + f)
    ser.write(h + req + f)

    # single wire serial will have echo
    echo = h + req + f
    res = ser.read(len(echo))
    if DEBUG: print('ECHO <---')
    if DEBUG: hexdump(res)
    assert len(res) == len(echo), 'TIMEOUT!'
    assert res == echo,  'EXPECTED TX TO MATCH ECHO!'

def get_response(ser):
    if DEBUG: print('RX <---')
    # read header (1 byte)
    h = ser.read()
    assert len(h) == 1, 'TIMEOUT!'
    # 0x11 = data frame
    if h != b'\x11':
        if DEBUG: hexdump(h + ser.read())
        raise Exception('EXPECTED DATA FRAME!')

    # read length (2 bytes)
    l = ser.read(2)
    res = l
    assert len(l) == 2, 'TIMEOUT!'
    l = struct.unpack('!H', l)[0]

    # read data
    d = ser.read(l)
    res += d
    assert len(d) == l, 'TIMEOUT!'

    # read checksum (1 byte)
    c = ser.read()
    assert len(c) == 1, 'TIMEOUT!'
    assert get_checksum(res) == c, 'INVALID CHECKSUM!'
    res += c

    # read footer (1 byte)
    f = ser.read()
    assert len(f) == 1, 'TIMEOUT!'

    if DEBUG: hexdump(h + res + f)
    return d, f[0]

def get_status(ser):
    d, f = get_response(ser)

    # TODO: not sure how to handle commands where length > 1
    # if len(d) != 1:
    #     raise Exception('EXPECTED STATUS LENGTH TO BE 1!')

    # 0x06 = normal acknowledgment
    if (d[0] if len(d) else None) != 0x06:
        raise Exception('EXPECTED NORMAL ACKNOWLEDGMENT!')

    # 0x03 = end of frame
    if f != 0x03:
        raise Exception('EXPECTED END OF FRAME!')

def get_data(ser):
    d, f = get_response(ser)

    # 0x03 = end of frame, 0x17 = more data
    if f != 0x03 and f != 0x17:
        raise Exception('EXPECTED END OF FRAME OR CONTINUE!')

    return d, f == 0x03

def send_acknowledgment(ser):
    send_request(ser, b'\x06')

def reset(ser):
    print('[RESET]')
    send_request(ser, b'\x00')
    get_status(ser)

def oscillating_frequency_set(ser, d01, d02, d03, d04):
    print('[OSCILLATING FREQUENCY SET] d01={} d02={} d03={} d04={}'.format(d01, d02, d03, d04))
    # oscillation frequency kHz = (D01 * 0.1 + D02 * 0.01 + D03 * 0.001) * 10D0
    send_request(ser, b'\x90', bytes([d01, d02, d03, d04]))
    get_status(ser)

def baud_rate_set(ser, d01):
    print('[BAUD RATE SET] d01={}'.format(d01))
    send_request(ser, b'\x9A', bytes([d01]))
    get_status(ser)
    time.sleep(0.35)
    assert d01 == 0x01, 'UNSUPPORTED BAUD RATE!'
    ser.baudrate = 115200

def memory_read(ser, start_addr, end_addr):
    print('[MEMORY READ] start_addr={} end_addr={}'.format(start_addr, end_addr))
    data = b''
    send_request(ser, b'\x50', struct.pack('!I', start_addr) + struct.pack('!I', end_addr))
    get_status(ser)
    done = False
    cnt = 0
    with tqdm(total=end_addr - start_addr + 1) as progress:
        while not done:
            send_acknowledgment(ser)
            d, done = get_data(ser)
            data += d
            progress.update(len(d))
            cnt += 1
    return data

# TODO: figure out the correct request ID
# def memory_checksum(ser, start_addr, end_addr):
#     print('[MEMORY CHECKSUM]')
#     send_request(ser, b'\xB0', struct.pack('!I', start_addr) + struct.pack('!I', end_addr))
#     data = get_status(ser)
#     return struct.unpack('!H', data)[0]

# TODO: figure out the correct request ID
# def silicon_signature(ser):
#     print('[SILICON SIGNATURE]')
#     send_request(ser, b'\xC0')
#     data = get_status(ser)
#     return struct.unpack('!H', data)[0]

if __name__ == "__main__":
    # example usage
    with serial.Serial(SERIALPORT, BAUDRATE, timeout=5.0) as ser:
        pulse(ser)
        reset(ser)
        # 16000 kHz
        oscillating_frequency_set(ser, 0x01, 0x06, 0x00, 0x05)
        # TODO: crappy tri-state buffer can't handle higher speed
        # # 115200 kbps
        # baud_rate_set(ser, 0x01)
        # reset(ser)

        start_addr = 0x00000000
        end_addr = 0x000FFFFF
        code = memory_read(ser, start_addr, end_addr)
        with open('code.bin', 'wb') as f:
            f.write(code)
        # TODO: not sure how to get checksum, but that is OK since fw has crc32 checksums in it
        # code_checksum = memory_checksum(ser, start_addr, end_addr)
        # checksum = sum(map(ord, code)) & 0xFFFF
        # assert code_checksum == checksum, "failed checksum validation"

        start_addr = 0x02000000
        end_addr = 0x02007FFF
        # TODO: fix progress - dumps double the bytes requested because each data byte has a tag
        data = memory_read(ser, start_addr, end_addr)
        with open('data.bin', 'wb') as f:
            f.write(data)
        # TODO: not sure how to get checksum, but that is OK since fw has crc32 checksums in it
        # data_checksum = memory_checksum(ser, start_addr, end_addr)
        # checksum = sum(map(ord, data)) & 0xFFFF
        # assert data_checksum == checksum, "failed checksum validation"

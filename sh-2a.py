import sys
import time
import struct
import serial
from hexdump import hexdump
from tqdm import tqdm

SERIALPORT = '/dev/ttyUSB0'
BAUDRATE = 9600
DEBUG = False

def handshake(ser):
    print('[HANDSHAKE]')

    ser.reset_input_buffer()
    ser.reset_output_buffer()

    ser.write(b'\x00' * 30)
    get_response(ser, b'\x00', no_data=True)
    send_request(ser, b'\x55')
    get_response(ser, b'\xE6', no_data=True)

def get_checksum(req):
    chksum = -sum(req)
    return bytes([chksum & 0xFF])

def send_request(ser, id, data=None):
    if DEBUG: print('TX --->')
    req = id
    if data and len(data):
        req += bytes([len(data)]) + data
        req += get_checksum(req)
    if DEBUG: hexdump(req)
    ser.write(req)

def get_response(ser, id, no_data=False, no_checksum=False, size_len=1):
    res = ser.read()
    assert len(res) == 1, 'TIMEOUT!'
    if res != id:
        if DEBUG: print('RX <---')
        if DEBUG: hexdump(res + ser.read())
        raise Exception('ERROR RESPONSE!')

    try:
        if no_data:
            return None

        size = ser.read(size_len)
        res += size
        assert len(size) == size_len, 'TIMEOUT!'

        if size_len == 1:
            byte_cnt = ord(size)
        elif size_len == 2:
            byte_cnt = struct.unpack('!H', size)[0]
        elif size_len == 4:
            byte_cnt = struct.unpack('!I', size)[0]
        else:
            raise Exception("invalid size_len: {}".format(size_len))

        data = ser.read(byte_cnt)
        res += data
        assert len(data) == byte_cnt, 'TIMEOUT!'

        if no_checksum:
            return data

        expect_checksum = get_checksum(res)
        actual_checksum = ser.read()
        res += actual_checksum
        assert len(actual_checksum) == 1, 'TIMEOUT!'
        assert expect_checksum == actual_checksum, 'INVALID CHECKSUM!'

        return data
    finally:
        if DEBUG: print('RX <---')
        if DEBUG: hexdump(res)

def device_inquiry(ser):
    print('[DEVICE INQUIRY]')
    send_request(ser, b'\x20')
    data = get_response(ser, b'\x30')

    devices = list()
    count = ord(data[0])
    idx = 1
    for i in range(count):
        char_count = ord(data[idx])
        devices.append(data[idx+1:idx+5]) # device code is 4 bytes
        idx += char_count # skip product code

    return devices

def device_select(ser, device):
    print('[DEVICE SELECT] device={}'.format(device))
    send_request(ser, b'\x10', device)
    get_response(ser, b'\x06', no_data=True)

def clock_inquiry(ser):
    print('[CLOCK INQUIRY]')
    send_request(ser, b'\x21')
    data = get_response(ser, b'\x31')

    clocks = list()
    for i in range(len(data)):
        clocks.append(ord(data[i]))

    return clocks

def clock_select(ser, clock):
    print('[CLOCK SELECT] clock={}'.format(clock))
    send_request(ser, b'\x11', bytes([clock]))
    get_response(ser, b'\x06', no_data=True)

def user_boot_mat_inquiry(ser):
    print('[USER BOOT MEMORY ADDR INQUIRY]')
    send_request(ser, b'\x24')
    data = get_response(ser, b'\x34')

    mat_count = ord(data[0])
    mat_ranges = list()
    for i in range(1, len(data), 8):
        mat_ranges.append({
            'start_addr': struct.unpack('!I', data[i:i+4])[0],
            'end_addr': struct.unpack('!I', data[i+4:i+8])[0],
        })

    return mat_ranges

def user_mat_inquiry(ser):
    print('[USER MEMORY ADDR INQUIRY]')
    send_request(ser, b'\x25')
    data = get_response(ser, b'\x35')

    mat_count = ord(data[0])
    mat_ranges = list()
    for i in range(1, len(data), 8):
        mat_ranges.append({
            'start_addr': struct.unpack('!I', data[i:i+4])[0],
            'end_addr': struct.unpack('!I', data[i+4:i+8])[0],
        })

    return mat_ranges

def multiplication_ratio_inquiry(ser):
    print('[MULTIPLICATION RATIO INQUIRY]')
    send_request(ser, b'\x22')
    data = get_response(ser, b'\x32')

    clock_type_count = ord(data[0])
    clock_multi_ratios = list()
    idx = 1
    for i in range(clock_type_count):
        ratio_count = ord(data[idx])
        idx += 1
        ratios = map(ord, data[idx:idx+ratio_count])
        clock_multi_ratios.append(ratios)
        idx += ratio_count

    return clock_multi_ratios

def operating_freq_inquiry(ser):
    print('[OPERATING FREQUENCY INQUIRY]')
    send_request(ser, b'\x23')
    data = get_response(ser, b'\x33')

    clock_type_count = ord(data[0])
    clock_freq_ranges = list()
    for i in range(1, 1+clock_type_count*4, 4):
        clock_freq_ranges.append({
            'min_mhz': struct.unpack('!H', data[i:i+2])[0] / 100,
            'max_mhz': struct.unpack('!H', data[i+2:i+4])[0] / 100,
        })

    return clock_freq_ranges

def bitrate_select(ser, baud_rate, input_freq_mhz, clock_count, ratio1, ratio2):
    print('[BITRATE SELECT] baud_rate={} input_freq_mhz={} clock_count={} ratio1={} ratio2={}'.format(baud_rate, input_freq_mhz, clock_count, ratio1, ratio2))
    send_request(ser, b'\x3F', struct.pack('!H', int(baud_rate/100)) + struct.pack('!H', int(input_freq_mhz*100)) + bytes(clock_count, ratio1, ratio2))
    get_response(ser, b'\x06', no_data=True)

    # wait 1 bit time step before changing
    time.sleep(1/ser.baudrate)
    ser.baudrate = baud_rate

    # confirmation    
    send_request(ser, b'\x06')
    get_response(ser, b'\x06', no_data=True)

def keycode_check(ser, key_code):
    print('[KEYCODE CHECK]')
    # transition to key-code determination state
    send_request(ser, b'\x40')
    get_response(ser, b'\x16', no_data=True)
    # perform key-code check
    send_request(ser, b'\x60', key_code)
    get_response(ser, b'\x26', no_data=True)

def status_inquiry(ser):
    print('[STATUS INQUIRY]')
    send_request(ser, b'\x4F')
    data = get_response(ser, b'\x5F', no_checksum=True)
    return {
        "status": data[0],
        "error": data[1],
    }

def read_memory(ser, mem_area, start, end, block_size):
    print('[READ MEMORY] area={} start={} end={} block_size={}'.format(mem_area, start, end, block_size))
    data = ''
    for i in tqdm(range(start, end, block_size)):
        send_request(ser, b'\x52', bytes([mem_area]) + struct.pack('!I', i) + struct.pack('!I', block_size))
        data += get_response(ser, b'\x52', size_len=4)
    return data

def user_boot_mat_checksum_inquiry(ser):
    print('[USER BOOT MEMORY CHECKSUM INQUIRY]')
    send_request(ser, b'\x4A')
    data = get_response(ser, b'\x5A')
    return struct.unpack('!I', data)[0]

def user_mat_checksum_inquiry(ser):
    print('[USER MEMORY CHECKSUM INQUIRY]')
    send_request(ser, b'\x4B')
    data = get_response(ser, b'\x5B')
    return struct.unpack('!I', data)[0]

if __name__ == "__main__":
    # example usage
    with serial.Serial(SERIALPORT, BAUDRATE, timeout=0.2) as ser:
        handshake(ser)

        devices = device_inquiry(ser)
        #print("devices: {}".format(devices))
        device_select(ser, devices[0])

        clocks = clock_inquiry(ser)
        #print("clocks: {}".format(clocks))
        clock_select(ser, clocks[0])

        multi_ratios = multiplication_ratio_inquiry(ser)
        #print("multiplication ratios: {}".format(multi_ratios))
        operating_freqs = operating_freq_inquiry(ser)
        #print("operating frequencies: {}".format(operating_freqs))
        ratio1 = multi_ratios[0][0]
        ratio2 = multi_ratios[1][0]
        base1 = operating_freqs[0]['max_mhz'] / ratio1
        base2 = operating_freqs[1]['max_mhz'] / ratio2
        assert base1 == base2, "failed to find base clock for both multipliers"
        bitrate_select(ser, BAUDRATE, base1, 2, ratio1, ratio2)

        user_boot_mat = user_boot_mat_inquiry(ser)
        #print("user boot memory area: {}".format(user_boot_mat))
        user_mat = user_mat_inquiry(ser)
        #print("user memory area: {}".format(user_mat))

        # any key code is accepted if the key code has not been set
        keycode = b'\x00' * 16
        keycode_check(ser, keycode)

        user_boot_mat_checksum = user_boot_mat_checksum_inquiry(ser)
        #print("user boot memory checksum: {}".format(user_boot_checksum))
        user_mat_checksum = user_mat_checksum_inquiry(ser)
        #print("user memory checksum: {}".format(user_mat_checksum))

        mem_area = 0 # user boot memory area
        start_addr = user_boot_mat[0]['start_addr']
        end_addr = user_boot_mat[0]['end_addr']
        data = read_memory(ser, mem_area, start_addr, end_addr+1, 0x40)
        with open('user_boot.bin', 'w+') as f:
            f.write(data)
        checksum = sum(map(ord, data)) & 0xFFFFFFFF
        assert user_boot_mat_checksum == checksum, "failed checksum validation"

        mem_area = 1 # user memory area
        start_addr = user_mat[0]['start_addr']
        end_addr = user_mat[0]['end_addr']
        data = read_memory(ser, mem_area, start_addr, end_addr+1, 0x40)
        with open('user.bin', 'w+') as f:
            f.write(data)
        checksum = sum(map(ord, data + keycode)) & 0xFFFFFFFF
        assert user_mat_checksum == checksum, "failed checksum validation"

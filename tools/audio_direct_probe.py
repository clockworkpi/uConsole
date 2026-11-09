"""Guest-side ctypes ALSA comparison; uploaded only into disposable validators."""

DIRECT_ALSA = r'''
def play_direct(source, device, raw_drain=False):
    import ctypes as c, json, types, wave
    lib = c.CDLL('libasound.so.2')
    def api(name, result, arguments):
        function = getattr(lib, name)
        function.restype, function.argtypes = result, arguments
        return function
    open_pcm = api('snd_pcm_open', c.c_int, [c.POINTER(c.c_void_p), c.c_char_p, c.c_int, c.c_int])
    set_params = api('snd_pcm_set_params', c.c_int, [c.c_void_p, c.c_int, c.c_int,
                                                   c.c_uint, c.c_uint, c.c_int, c.c_uint])
    get_params = api('snd_pcm_get_params', c.c_int, [c.c_void_p, c.POINTER(c.c_ulong), c.POINTER(c.c_ulong)])
    write = api('snd_pcm_writei', c.c_long, [c.c_void_p, c.c_void_p, c.c_ulong])
    delay = api('snd_pcm_delay', c.c_int, [c.c_void_p, c.POINTER(c.c_long)])
    drain = api('snd_pcm_drain', c.c_int, [c.c_void_p])
    close = api('snd_pcm_close', c.c_int, [c.c_void_p])
    format_value = api('snd_pcm_format_value', c.c_int, [c.c_char_p])
    error = api('snd_strerror', c.c_char_p, [c.c_int])
    def check(code):
        if code < 0: raise RuntimeError(error(code).decode())
        return code
    with wave.open(str(source), 'rb') as stream:
        if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) != (2, 2, 48000):
            raise ValueError('Unexpected direct ALSA input format')
        frames = stream.getnframes()
        payload = stream.readframes(frames)
        if frames != 48000 or len(payload) != frames * 4:
            raise ValueError('Direct ALSA probe requires exactly 48000 complete input frames')
    handle = c.c_void_p()
    check(open_pcm(c.byref(handle), device.encode(), 0, 0))
    try:
        # ALSA's public SND_PCM_ACCESS_RW_INTERLEAVED enum is 3.
        check(set_params(handle, format_value(b'S16_LE'), 3, 2, 48000, 0, 500000))
        buffer_frames, period_frames = c.c_ulong(), c.c_ulong()
        check(get_params(handle, c.byref(buffer_frames), c.byref(period_frames)))
        data = c.create_string_buffer(payload)
        written = 0
        while written < frames:
            count = check(write(handle, c.byref(data, written * 4), frames - written))
            if not 0 < count <= frames - written: raise RuntimeError('Invalid ALSA write count')
            written += count
        pending = c.c_long()
        check(delay(handle, c.byref(pending)))
        if raw_drain:
            import fcntl
            class PollFD(c.Structure):
                _fields_ = [('fd', c.c_int), ('events', c.c_short), ('revents', c.c_short)]
            poll_count = api('snd_pcm_poll_descriptors_count', c.c_int, [c.c_void_p])
            poll_descriptors = api('snd_pcm_poll_descriptors', c.c_int,
                                   [c.c_void_p, c.POINTER(PollFD), c.c_uint])
            if check(poll_count(handle)) != 1:
                raise ValueError('Raw drain requires a single hardware PCM descriptor')
            descriptor = PollFD()
            if check(poll_descriptors(handle, c.byref(descriptor), 1)) != 1:
                raise ValueError('Missing hardware PCM descriptor')
            # Linux UAPI SNDRV_PCM_IOCTL_DRAIN = _IO('A', 0x44).
            fcntl.ioctl(descriptor.fd, 0x4144)
        else:
            check(drain(handle))
        details = {'client':'direct-libasound', 'written_frames':written,
                   'delay_before_drain':pending.value, 'drain_exit_code':0,
                   'buffer_frames':buffer_frames.value, 'period_frames':period_frames.value}
        details['drain_method'] = 'kernel-ioctl' if raw_drain else 'libasound'
    finally:
        check(close(handle))
    return types.SimpleNamespace(returncode=0, stderr='', stdout=json.dumps(details))
'''

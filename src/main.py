import sys, os, threading, subprocess, queue, time
from Quartz import (
    CGEventTapCreate, kCGHIDEventTap, kCGHeadInsertEventTap, kCGEventTapOptionDefault,
    CGEventMaskBit, kCGEventKeyDown, kCGEventKeyUp, kCGEventFlagsChanged,
    CFRunLoopGetCurrent, CFRunLoopRun, CFMachPortCreateRunLoopSource,
    CFRunLoopAddSource, kCFRunLoopCommonModes, CGEventGetFlags, CGEventGetIntegerValueField,
    kCGKeyboardEventKeycode, kCGEventFlagMaskAlternate, CGEventTapEnable,
    kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput
)
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

HOPBOP_CONFIG = os.path.expanduser('~/.hopbop')
HOTKEY_KEYCODES = [18, 19, 20, 21, 23, 22, 26, 28, 25]

MAPPING_LOCK = threading.Lock()
mapping = {}

# --- NEW: async launcher ---
_launch_q = queue.Queue()
def _launcher():
    while True:
        bundle_id = _launch_q.get()
        if bundle_id is None:
            return
        try:
            # Non-blocking; let LaunchServices do the work
            subprocess.Popen(["open", "-b", bundle_id])
        except Exception as e:
            print(f"[HopBop] Failed to launch: {e}")
        finally:
            _launch_q.task_done()

_launcher_thread = None

# --- Track state to debounce repeats ---
_pressed_fired = set()   # keycodes fired while Option is held
_alt_down = False

def load_mappings():
    global mapping
    try:
        with open(HOPBOP_CONFIG, 'r') as f:
            lines = [line.strip() for line in f if line.strip()]
            new_map = {code: bundle_id for code, bundle_id in zip(HOTKEY_KEYCODES, lines)}
        with MAPPING_LOCK:
            mapping = new_map
        for i, v in enumerate(mapping.values()):
            print(f"[{i + 1}] -> {v}")
    except Exception as e:
        print(f"[HopBop] Failed to load config: {e}")

class HopBopConfigHandler(FileSystemEventHandler):
    def on_modified(self, event):
        # Some editors write to a temp file then replace; be flexible
        if os.path.abspath(event.src_path) == os.path.abspath(HOPBOP_CONFIG) or \
           os.path.basename(event.src_path) == os.path.basename(HOPBOP_CONFIG):
            # small debounce in case of rapid successive writes
            time.sleep(0.05)
            print("[HopBop] Config file changed. Reloading...")
            load_mappings()

def start_config_watcher():
    event_handler = HopBopConfigHandler()
    observer = Observer()
    config_dir = os.path.dirname(HOPBOP_CONFIG) or '.'
    observer.schedule(event_handler, path=config_dir, recursive=False)
    observer.start()

# Keep a reference to the tap so we can re-enable it
_tap_port = None

def tap_callback(proxy, type_, event, refcon):
    # Handle tap disabled cases promptly
    if type_ in (kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput):
        if _tap_port is not None:
            CGEventTapEnable(_tap_port, True)
        return event

    global _alt_down

    if type_ == kCGEventFlagsChanged:
        flags = CGEventGetFlags(event)
        was_down = _alt_down
        _alt_down = bool(flags & kCGEventFlagMaskAlternate)
        # If Option was released, clear debounce state
        if was_down and not _alt_down:
            _pressed_fired.clear()
        return event

    if type_ == kCGEventKeyDown:
        keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
        flags = CGEventGetFlags(event)

        if flags & kCGEventFlagMaskAlternate:
            with MAPPING_LOCK:
                bundle_id = mapping.get(keycode)
            if bundle_id and keycode not in _pressed_fired:
                _pressed_fired.add(keycode)  # debounce this key until it’s released or Alt released
                # enqueue launch and return quickly
                _launch_q.put(bundle_id)
                return None  # swallow the keystroke if you want
    elif type_ == kCGEventKeyUp:
        keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
        # Release debounce for this key
        if keycode in _pressed_fired:
            _pressed_fired.discard(keycode)

    return event

def main():
    print("[HopBop] Starting...")

    load_mappings()
    start_config_watcher()

    # Start async launcher
    global _launcher_thread
    _launcher_thread = threading.Thread(target=_launcher, daemon=True)
    _launcher_thread.start()

    event_mask = (
        CGEventMaskBit(kCGEventKeyDown) |
        CGEventMaskBit(kCGEventKeyUp) |
        CGEventMaskBit(kCGEventFlagsChanged)
    )

    global _tap_port
    _tap_port = CGEventTapCreate(
        kCGHIDEventTap,
        kCGHeadInsertEventTap,
        kCGEventTapOptionDefault,
        event_mask,
        tap_callback,
        None
    )

    if not _tap_port:
        print("[HopBop] ERROR: Couldn't create event tap. Check Accessibility permissions (System Settings → Privacy & Security → Accessibility).")
        sys.exit(1)

    runLoopSource = CFMachPortCreateRunLoopSource(None, _tap_port, 0)
    CFRunLoopAddSource(CFRunLoopGetCurrent(), runLoopSource, kCFRunLoopCommonModes)

    print("[HopBop] Listening for Option+1..9 hotkeys. Ctrl+C to quit.")
    CFRunLoopRun()

if __name__ == "__main__":
    main()


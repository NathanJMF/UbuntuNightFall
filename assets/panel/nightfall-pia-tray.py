"""Recolour the VPN's live lock artwork while keeping its status badges."""
import hashlib
from pathlib import Path
import signal
import sys

from gi.repository import Gio, GLib, GLibUnix
from PIL import Image


WATCHER = 'org.kde.StatusNotifierWatcher'
ITEM = 'org.kde.StatusNotifierItem'
ITEM_ID = 'privateinternetaccess'
PROPERTIES = 'org.freedesktop.DBus.Properties'
STORE = Path(GLib.get_user_runtime_dir()) / 'nightfall-pia-tray'


def rendered(pixmaps, directory):
    valid = [(w, h, p) for w, h, p in pixmaps
             if 0 < w <= 256 and 0 < h <= 256 and len(p) == w * h * 4]
    if not valid:
        return ''
    w, h, data = max(valid, key=lambda item: item[0] * item[1])
    raw = bytes(data)
    digest = hashlib.sha256(bytes([w % 256, h % 256]) + raw).hexdigest()[:24]
    path = directory / f'pia-{digest}.png'
    if not path.exists():
        icon = Image.frombytes('RGBA', (w, h), raw, 'raw', 'ARGB')
        # The light artwork has a bright neutral lock. Coloured connection
        # and alert badges and the transparency are supplied directly by PIA.
        pixels = []
        for r, g, b, a in icon.getdata():
            low, high = min(r, g, b), max(r, g, b)
            amount = max(0, min(1, (low - 140) / 70))
            amount *= max(0, min(1, (45 - high + low) / 37))
            pixels.append((round(r * (1 - 0.07 * amount)),
                           round(g * (1 - 0.61 * amount)),
                           round(b * (1 - 0.63 * amount)), a))
        icon.putdata(pixels)
        temp = path.with_suffix('.tmp')
        icon.save(temp, format='PNG')
        temp.replace(path)
    return str(path)


def update_mapping(settings, paths=None):
    rows = settings.get_value('custom-icons').unpack()
    ours = lambda row: row[0] == ITEM_ID and Path(row[1]).parent == STORE
    new = [row for row in rows if not ours(row)]
    if paths and not any(row[0] == ITEM_ID for row in new):
        new.append((ITEM_ID, *paths))
    if new != rows:
        if not settings.set_value('custom-icons', GLib.Variant('a(sss)', new)):
            raise RuntimeError('GNOME did not accept the tray icon mapping')


class PiaTray:
    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.settings = Gio.Settings.new('org.gnome.shell.extensions.appindicator')
        self.source = None
        self.signals = []
        self.pending = 0

    def call(self, service, path, interface, method, args):
        reply = self.bus.call_sync(service, path, interface, method, args, None,
                                   Gio.DBusCallFlags.NO_AUTO_START, 1500, None)
        return reply.unpack()[0]

    def discover(self):
        entries = self.call(WATCHER, '/StatusNotifierWatcher', PROPERTIES, 'Get',
                            GLib.Variant('(ss)', (WATCHER, 'RegisteredStatusNotifierItems')))
        for entry in entries:
            service, sep, suffix = entry.replace('@/', '/', 1).partition('/')
            path = '/' + suffix if sep else '/StatusNotifierItem'
            try:
                pid = self.call('org.freedesktop.DBus', '/org/freedesktop/DBus',
                                'org.freedesktop.DBus', 'GetConnectionUnixProcessID',
                                GLib.Variant('(s)', (service,)))
                if Path(f'/proc/{pid}/exe').resolve() != Path('/opt/piavpn/bin/pia-client'):
                    continue
                if self.call(service, path, PROPERTIES, 'Get',
                             GLib.Variant('(ss)', (ITEM, 'Id'))) == ITEM_ID:
                    return service, path
            except (GLib.Error, OSError):
                continue
        return None

    def render_current(self, directory):
        props = self.call(*self.source, PROPERTIES, 'GetAll', GLib.Variant('(s)', (ITEM,)))
        if props.get('Status') == 'Passive':
            return None
        normal = rendered(props.get('IconPixmap', []), directory)
        attention = rendered(props.get('AttentionIconPixmap', []), directory)
        if props.get('Status') == 'NeedsAttention' and not attention:
            return None
        return (normal, attention or props.get('AttentionIconName') or normal) if normal else None

    def refresh(self):
        self.pending = 0
        try:
            paths = self.render_current(STORE) if self.source else None
            update_mapping(self.settings, paths)
            current = set(paths or ())
            for old in sorted(STORE.glob('pia-*.png'), key=lambda p: p.stat().st_mtime,
                              reverse=True)[16:]:
                if str(old) not in current:
                    old.unlink(missing_ok=True)
        except (GLib.Error, OSError, ValueError):
            update_mapping(self.settings)
        return GLib.SOURCE_REMOVE

    def schedule(self, *unused):
        if not self.pending:
            self.pending = GLib.idle_add(self.refresh)

    def rescan(self, *unused):
        try:
            source = self.discover()
        except GLib.Error:
            source = None
        if source != self.source:
            for token in self.signals:
                self.bus.signal_unsubscribe(token)
            self.signals.clear()
            self.source = source
            if source:
                service, path = source
                for iface in (ITEM, PROPERTIES):
                    self.signals.append(self.bus.signal_subscribe(
                        service, iface, None, path, None, Gio.DBusSignalFlags.NONE,
                        self.schedule))
                self.signals.append(self.bus.signal_subscribe(
                    'org.freedesktop.DBus', 'org.freedesktop.DBus', 'NameOwnerChanged',
                    '/org/freedesktop/DBus', service, Gio.DBusSignalFlags.NONE, self.rescan))
        self.schedule()

    def run(self):
        STORE.mkdir(mode=0o700, exist_ok=True)
        update_mapping(self.settings)
        self.bus.signal_subscribe(WATCHER, WATCHER, None, '/StatusNotifierWatcher', None,
                                  Gio.DBusSignalFlags.NONE, self.rescan)
        Gio.bus_watch_name_on_connection(self.bus, WATCHER, Gio.BusNameWatcherFlags.NONE,
                                         self.rescan, self.rescan)
        loop = GLib.MainLoop()
        def stop(*unused):
            loop.quit()
            return GLib.SOURCE_REMOVE

        for signum in (signal.SIGINT, signal.SIGTERM):
            GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signum, stop)
        try:
            loop.run()
        finally:
            update_mapping(self.settings)
            Gio.Settings.sync()


if __name__ == '__main__':
    tray = PiaTray()
    if sys.argv[1:] == ['--clear']:
        update_mapping(tray.settings)
        Gio.Settings.sync()
    elif len(sys.argv) == 3 and sys.argv[1] == '--preview':
        tray.source = tray.discover()
        if not tray.source:
            raise SystemExit('PIA has no registered tray item')
        print(tray.render_current(Path(sys.argv[2])))
    elif len(sys.argv) == 1:
        tray.run()
    else:
        raise SystemExit('Expected no options, --clear, or --preview DIRECTORY')

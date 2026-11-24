"""Install or restore Nightfall for the current desktop user."""
from base64 import b64decode, b64encode
from hashlib import sha256
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from xml.etree import ElementTree

import gi
gi.require_version('GioUnix','2.0')
from gi.repository import Gio, GioUnix, GLib

ROOT = Path(__file__).resolve().parent
HOME = Path.home()
DATA = Path(os.environ.get('XDG_DATA_HOME', HOME / '.local/share'))
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', HOME / '.config'))
STATE = Path(os.environ.get('XDG_STATE_HOME', HOME / '.local/state')) / 'ubuntu-nightfall'
RECORD = STATE / 'originals.json'
UUID = 'nightfall-popups@ubuntu-nightfall.local'
ASSETS = ROOT / 'assets'
THEME = DATA / 'ubuntu-nightfall'
ICON_NAME = 'Nightfall-Ruby'
ALIASES = {
    'antigravity': 'nightfall-antigravity', 'bitwarden_bitwarden': 'nightfall-bitwarden',
    'chromium_chromium': 'nightfall-chromium', 'chatgpt': 'chatgpt',
    'io.github.keepassxcagentaccess.Tray': 'nightfall-keeper',
    'io.local.MacBookThermals': 'nightfall-thermals', 'piavpn': 'piavpn',
    'keepassxc_keepassxc': 'nightfall-keepassxc', 'net.nokyan.Resources': 'nightfall-resources',
    'net.nokyan.Resources.Devel': 'nightfall-custom-resources',
    'pycharm-community_pycharm-community': 'nightfall-pycharm',
    'pycharm_pycharm': 'nightfall-pycharm',
    'org.gnome.Nautilus': 'org.gnome.Nautilus',
}
for icon in (ASSETS / 'icons/scalable/apps').glob('nightfall-*_*.*'):
    if icon.suffix in {'.png', '.svg'}:
        ALIASES[icon.stem.removeprefix('nightfall-')] = icon.stem


def run(*command):
    return subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()


def atomic(path, body, mode=0o644):
    path.parent.mkdir(exist_ok=True, parents=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        try:
            temp.write(body)
            temp.flush()
            os.fchmod(temp.fileno(), mode)
            os.replace(temp.name, path)
        finally:
            Path(temp.name).unlink(missing_ok=True)


class Desktop:
    def __init__(self):
        self.record = json.loads(RECORD.read_text()) if RECORD.exists() else {'files': {}, 'settings': {}}

    def save(self):
        atomic(RECORD, json.dumps(self.record).encode(), 0o600)

    def file(self, path, body):
        if path.is_symlink():
            raise ValueError(f'Existing symbolic link at {path}')
        before = path.read_bytes() if path.exists() else None
        if before == body:
            return
        files = self.record['files']
        entry = files.get(str(path))
        if entry is None:
            entry = files[str(path)] = {'old': b64encode(before).decode() if before is not None else None,
                                      'mode': path.stat().st_mode & 0o777 if before is not None else 0o644}
        elif before is not None and sha256(before).hexdigest() != entry['installed']:
            print(f'Keeping edited file: {path}', file=sys.stderr)
            return
        entry['installed'] = sha256(body).hexdigest()
        self.save()
        atomic(path, body, entry['mode'])

    def tree(self, src, dst):
        for file in sorted(src.rglob('*')):
            if file.is_file():
                self.file(dst / file.relative_to(src), file.read_bytes())

    def setting(self, path, value):
        settings = self.record['settings']
        if path not in settings:
            settings[path] = {'old': run('dconf', 'read', path)}
        settings[path]['installed'] = value
        self.save()
        run('dconf', 'write', path, value)

    def string(self, path, value):
        self.setting(path, GLib.Variant('s', value).print_(True))

    def panorama(self, picture, name):
        catalog = ElementTree.Element('wallpapers')
        item = ElementTree.SubElement(catalog, 'wallpaper')
        for tag, value in [('name', name), ('filename', str(picture)),
                           ('filename-dark', str(picture)), ('options', 'spanned')]:
            ElementTree.SubElement(item, tag).text = value
        self.file(DATA / 'gnome-background-properties' / (picture.stem + '.xml'),
                  ElementTree.tostring(catalog, encoding='utf-8', xml_declaration=True))

    def install(self):
        self.tree(ASSETS / 'icons', DATA / 'icons' / ICON_NAME)
        self.tree(ASSETS / 'panel', THEME / 'panel')
        self.tree(ASSETS / 'surfaces', THEME / 'surfaces')
        pictures = Path(run('xdg-user-dir', 'PICTURES')) / 'backgrounds'
        for picture in (ASSETS / 'wallpapers').glob('[0-9]*.png'):
            self.file(pictures / picture.name, picture.read_bytes())
        panorama = ASSETS / 'wallpapers/nightfall-original-scale-6200x1920.png'
        self.file(pictures / panorama.name, panorama.read_bytes())
        self.panorama(pictures / panorama.name, 'Nightfall Panorama · Original Scale')
        self.tree(ROOT / 'extension', DATA / 'gnome-shell/extensions' / UUID)
        self.file(DATA / 'org.gnome.Ptyxis/palettes/Nightfall.palette', (ASSETS / 'terminal/Nightfall.palette').read_bytes())
        for version in ('3','4'):
            path = CONFIG / f'gtk-{version}.0/gtk.css'
            include = f'@import url("{(THEME / "surfaces" / ("gtk" + version + ".css")).as_uri()}");\n'.encode()
            css = path.read_bytes() if path.exists() else b''
            if include not in css:
                self.file(path, include + css)
        dialog = DATA / 'themes/Nightfall-Apport/gtk-3.0'
        css = (ASSETS / 'surfaces/apport.css').read_bytes()
        self.file(dialog / 'gtk.css', css)
        self.file(dialog / 'gtk-dark.css', css)
        resource = Path('/usr/share/themes/Yaru-red-dark/gtk-3.0/gtk.gresource')
        if resource.is_file():
            self.file(dialog / 'gtk.gresource', resource.read_bytes())
        self.file(CONFIG / 'systemd/user/update-notifier-crash.service.d/80-nightfall.conf',
                  b'[Service]\nEnvironment=GTK_THEME=Nightfall-Apport\n')
        overlay = '/org/gnome/Settings/style-dark.css=' + str(THEME / 'surfaces/settings.css')
        if 'previous_overlay' not in self.record:
            env = run('systemctl','--user','show-environment').splitlines()
            self.record['previous_overlay'] = next((line.partition('=')[2] for line in env
                                                    if line.startswith('G_RESOURCE_OVERLAYS=')), None)
        previous = self.record['previous_overlay']
        if previous and overlay not in previous.split(os.pathsep):
            overlay = previous + os.pathsep + overlay
        self.record['overlay'] = overlay
        self.save()
        self.file(CONFIG / 'environment.d/81-ubuntu-nightfall.conf', f'G_RESOURCE_OVERLAYS="{overlay}"\n'.encode())
        for app, alias in ALIASES.items():
            try:
                entry = GioUnix.DesktopAppInfo.new(app + '.desktop')
            except TypeError:
                continue  # This optional application is absent.
            if entry and entry.get_string('Icon') != alias:
                source = Path(entry.get_filename()).read_bytes()
                body = re.sub(rb'^Icon=[^\r\n]*', ('Icon=' + alias).encode(), source, count=1, flags=re.M)
                self.file(DATA / 'applications' / (app + '.desktop'), body)
        iface = '/org/gnome/desktop/interface/'
        for key, value in {'icon-theme':ICON_NAME, 'gtk-theme':'Yaru-red-dark', 'accent-color':'red', 'color-scheme':'prefer-dark'}.items():
            self.string(iface + key, value)
        for key in ('background/picture-uri', 'background/picture-uri-dark', 'screensaver/picture-uri'):
            self.string('/org/gnome/desktop/' + key, (pictures / '08-nightfall.png').as_uri())
        self.string('/org/gnome/shell/extensions/dash-to-panel/show-apps-icon-file', str(THEME / 'panel/show-apps-ruby.svg'))
        schema = Gio.SettingsSchemaSource.get_default()
        if schema.lookup('org.gnome.Ptyxis', True):
            profile = Gio.Settings.new('org.gnome.Ptyxis').get_string('default-profile-uuid')
            if profile:
                self.string('/org/gnome/Ptyxis/Profiles/' + profile + '/palette', 'Nightfall')
        if schema.lookup('org.gnome.shell.extensions.appindicator', True):
            settings = Gio.Settings.new('org.gnome.shell.extensions.appindicator')
            custom = settings.get_value('custom-icons').unpack()
            parts = {'chrome_status_icon_1':'chatgpt','Bitwarden_status_icon_1':'bitwarden'}
            mapped = [t for t in custom if t[0] not in parts]
            for name, art in parts.items():
                mapped.append((name, str(THEME / 'panel' / (art+'.png')), str(THEME / 'panel' / (art+'-attention.png'))))
            self.setting('/org/gnome/shell/extensions/appindicator/custom-icons', GLib.Variant('a(sss)',mapped).print_(True))
            unit = (ASSETS / 'panel/nightfall-pia-tray.service').read_text().replace('%h/.local/libexec/nightfall-pia-tray.py', '"'+str(THEME / 'panel/nightfall-pia-tray.py')+'"')
            self.file(CONFIG / 'systemd/user/nightfall-pia-tray.service', unit.encode())
            run('systemctl','--user','daemon-reload')
            run('systemctl','--user','enable','--now','nightfall-pia-tray.service')
        shell = Gio.Settings.new('org.gnome.shell')
        for key, want in [('enabled-extensions',True),('disabled-extensions',False)]:
            items = shell.get_strv(key)
            self.record.setdefault('extension',{}).setdefault(key,UUID in items)
            self.save()
            if (UUID in items) != want:
                shell.set_strv(key, [*items, UUID] if want else [i for i in items if i != UUID])
        Gio.Settings.sync()
        run('systemctl','--user','daemon-reload')
        run('dbus-update-activation-environment','--systemd','G_RESOURCE_OVERLAYS='+overlay)

    def restore(self):
        if not RECORD.exists():
            return
        service = CONFIG / 'systemd/user/nightfall-pia-tray.service'
        if str(service) in self.record['files'] and service.is_file():
            run('systemctl','--user','disable','--now','nightfall-pia-tray.service')
        shell = Gio.Settings.new('org.gnome.shell')
        for key, included in self.record.get('extension',{}).items():
            items = shell.get_strv(key)
            if (UUID in items) != included:
                shell.set_strv(key, [*items, UUID] if included else [i for i in items if i != UUID])
        Gio.Settings.sync()
        for path, entry in self.record['settings'].items():
            installed = GLib.Variant.parse(None, entry['installed'], None, None)
            current = run('dconf','read',path)
            if current and GLib.Variant.parse(installed.get_type(), current, None, None).equal(installed):
                if entry['old']:
                    run('dconf','write',path,entry['old'])
                else:
                    run('dconf','reset',path)
        conflicts = {}
        for name, entry in self.record['files'].items():
            path = Path(name)
            if path.is_symlink() or (path.exists() and sha256(path.read_bytes()).hexdigest() != entry['installed']):
                print(f'Keeping edited file: {path}',file=sys.stderr)
                conflicts[name] = entry
            elif entry['old'] is None:
                path.unlink(missing_ok=True)
            else:
                atomic(path,b64decode(entry['old']),entry['mode'])
        overlay = self.record.get('overlay')
        current_env = run('systemctl','--user','show-environment').splitlines()
        if overlay and ('G_RESOURCE_OVERLAYS='+overlay) in current_env:
            previous = self.record.get('previous_overlay')
            run('dbus-update-activation-environment','--systemd','G_RESOURCE_OVERLAYS='+(previous or ''))
            if previous is None:
                run('systemctl','--user','unset-environment','G_RESOURCE_OVERLAYS')
        if conflicts:
            self.record = {'files':conflicts,'settings':{}}
            self.save()
        else:
            RECORD.unlink()
        run('systemctl','--user','daemon-reload')


if __name__ == '__main__':
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        desktop = Desktop()
        {'install':desktop.install, 'restore':desktop.restore}[sys.argv[1]]()

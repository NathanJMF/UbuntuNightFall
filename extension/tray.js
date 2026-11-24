import Clutter from 'gi://Clutter';
import Cogl from 'gi://Cogl';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const effectName = 'nightfall-tray-colours';
const schemaId = 'org.gnome.shell.extensions.appindicator';

// Electron adds an instance number to the application's indicator ID.
function paletteFor(id) {
    const name = id.toLowerCase().replace(/_status_icon_[0-9]+$/, '');
    if (name === 'discord')
        return 1; // Only recolour the neutral glyph. Voice and unread badges carry colour.
    if (['keeper', 'keepassxc-agent-access', 'keepassxc', 'software-update-available',
        'unattended-upgrade', 'livepatch', 'thunderbird', 'remmina', 'remmina applet',
        'remmina-icon', 'org.remmina.remmina'].includes(name))
        return 2; // Retain warning and success colours in the app's own state artwork.
    if (['spotify', 'spotify-client', 'steam', 'md.obsidian.obsidian',
        'obsidian', 'transmission', 'transmission-gtk'].includes(name))
        return 0;
    return null;
}

const TrayColour = GObject.registerClass(class NightfallTrayColour extends Shell.GLSLEffect {
    _init(mode) {
        super._init();
        this.set_uniform_float(this.get_uniform_location('mode'), 1, [mode]);
    }

    vfunc_build_pipeline() {
        this.add_glsl_snippet(Cogl.SnippetHook.FRAGMENT, 'uniform float mode;', `
            float alpha = cogl_color_out.a;
            vec3 rgb = cogl_color_out.rgb / max(alpha, 0.001);
            float peak = max(rgb.r, max(rgb.g, rgb.b));
            float chroma = peak - min(rgb.r, min(rgb.g, rgb.b));
            vec3 brick = vec3(200.0, 74.0, 64.0) / 255.0;
            vec3 colour = brick * smoothstep(0.0, 0.85, peak);
            bool badge = mode == 1.0 && chroma > 0.15;
            bool state = mode == 2.0 && chroma > 0.15 && max(rgb.r, rgb.g) > rgb.b;
            cogl_color_out.rgb = (badge || state ? rgb : colour) * alpha;
        `, false);
    }
});

export class NightfallTray {
    enable() {
        this.watched = new Map();
        this.boxes = [Main.panel._leftBox, Main.panel._centerBox, Main.panel._rightBox];
        this.signals = this.boxes.map(box => [
            box.connect('child-added', (_box, child) => this.watch(child)),
            box.connect('child-removed', (_box, child) => this.unwatch(child)),
        ]);
        const schema = Gio.SettingsSchemaSource.get_default().lookup(schemaId, true);
        if (schema) {
            this.settings = Gio.Settings.new_full(schema, null, null);
            this.settingsSignal = this.settings.connect('changed::custom-icons', () => this.refresh());
        }
        this.refresh();
        for (const box of this.boxes)
            box.get_children().forEach(child => this.watch(child));
    }

    watch(child) {
        const button = child.get_child?.();
        if (!button?.icon || (!button._indicator && !button.has_style_class_name('tray-icon')))
            return;
        // New indicators can join the panel before their D-Bus ID is available.
        const signal = button.connect('notify::mapped', () => this.style(child));
        this.watched.set(child, [button, signal]);
        this.style(child);
    }

    unwatch(child) {
        const pair = this.watched.get(child);
        if (pair) {
            pair[0].disconnect(pair[1]);
            this.clear(child);
            this.watched.delete(child);
        }
    }

    refresh() {
        const overrides = this.settings?.get_value('custom-icons').deepUnpack() ?? [];
        this.custom = new Set(overrides.filter(([, normal, attention]) => normal || attention)
            .map(([id]) => id));
        for (const child of this.watched.keys())
            this.style(child);
    }

    clear(child) {
        const icon = child.get_child?.()?.icon;
        if (icon?.get_effect(effectName))
            icon.remove_effect_by_name(effectName);
    }

    style(child) {
        const button = child.get_child?.();
        const icon = button?.icon;
        if (!icon?.get_effect)
            return;
        const legacy = button.has_style_class_name('tray-icon');
        const id = legacy ? icon.wm_class : button._indicator?.id;
        if (legacy && /^bitwarden(?:-app)?$/i.test(id ?? '')) {
            if (!icon.get_effect(effectName)) {
                const [, tint] = Cogl.Color.from_string('#f07a84');
                icon.add_effect_with_name(effectName, new Clutter.ColorizeEffect({tint}));
            }
            return;
        }
        const mode = id && (legacy || !this.custom?.has(id)) ? paletteFor(id) : null;
        if (mode === null) {
            this.clear(child);
        } else if (!icon.get_effect(effectName)) {
            icon.add_effect_with_name(effectName, new TrayColour(mode));
        }
    }

    disable() {
        if (this.settings)
            this.settings.disconnect(this.settingsSignal);
        this.boxes.forEach((box, index) => {
            this.signals[index].forEach(signal => box.disconnect(signal));
        });
        for (const child of this.watched.keys())
            this.unwatch(child);
        this.settings = null;
        this.boxes = this.signals = this.custom = null;
    }
}
